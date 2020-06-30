# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from enum import Enum
from functools import partial
from typing import AsyncGenerator, Callable, List, Optional

import trio
from async_generator import asynccontextmanager
from structlog import get_logger

from parsec.api.data import EntryID
from parsec.api.protocol import AUTHENTICATED_CMDS, APIEvent, DeviceID
from parsec.core.backend_connection import cmds
from parsec.core.backend_connection.exceptions import BackendConnectionRefused, BackendNotAvailable
from parsec.core.backend_connection.expose_cmds import expose_cmds_with_retrier
from parsec.core.backend_connection.transport import TransportPool, connect_as_authenticated
from parsec.core.core_events import CoreEvent
from parsec.core.types import BackendOrganizationAddr
from parsec.crypto import SigningKey
from parsec.event_bus import EventBus

logger = get_logger()


BackendConnStatus = Enum("BackendConnStatus", "READY LOST INITIALIZING REFUSED CRASHED")


class BackendAuthenticatedCmds:
    def __init__(self, addr: BackendOrganizationAddr, acquire_transport):
        self.addr = addr
        self.acquire_transport = acquire_transport

    for cmd_name in AUTHENTICATED_CMDS:
        vars()[cmd_name] = expose_cmds_with_retrier(cmd_name)


def _handle_event(event_bus: EventBus, rep: dict) -> None:
    if rep["status"] != "ok":
        logger.warning("Bad response to `events_listen` command", rep=rep)
        return

    if rep["event"] == APIEvent.MESSAGE_RECEIVED:
        event_bus.send(CoreEvent.BACKEND_MESSAGE_RECEIVED, index=rep["index"])

    elif rep["event"] == APIEvent.PINGED:
        event_bus.send(CoreEvent.BACKEND_PINGED, ping=rep["ping"])

    elif rep["event"] == APIEvent.REALM_ROLES_UPDATED:
        realm_id = EntryID(rep["realm_id"])
        event_bus.send(CoreEvent.BACKEND_REALM_ROLES_UPDATED, realm_id=realm_id, role=rep["role"])

    elif rep["event"] == APIEvent.REALM_VLOBS_UPDATED:
        src_id = EntryID(rep["src_id"])
        realm_id = EntryID(rep["realm_id"])
        event_bus.send(
            CoreEvent.BACKEND_REALM_VLOBS_UPDATED,
            realm_id=realm_id,
            checkpoint=rep["checkpoint"],
            src_id=src_id,
            src_version=rep["src_version"],
        )

    elif rep["event"] == APIEvent.REALM_MAINTENANCE_STARTED:
        event_bus.send(
            CoreEvent.BACKEND_REALM_MAINTENANCE_STARTED,
            realm_id=rep["realm_id"],
            encryption_revision=rep["encryption_revision"],
        )

    elif rep["event"] == APIEvent.REALM_MAINTENANCE_FINISHED:
        event_bus.send(
            CoreEvent.BACKEND_REALM_MAINTENANCE_FINISHED,
            realm_id=rep["realm_id"],
            encryption_revision=rep["encryption_revision"],
        )


def _transport_pool_factory(addr, device_id, signing_key, max_pool, keepalive):
    async def _connect():
        transport = await connect_as_authenticated(
            addr, device_id=device_id, signing_key=signing_key, keepalive=keepalive
        )
        transport.logger = transport.logger.bind(device_id=device_id)
        return transport

    return TransportPool(_connect, max_pool=max_pool)


class BackendAuthenticatedConn:
    def __init__(
        self,
        addr: BackendOrganizationAddr,
        device_id: DeviceID,
        signing_key: SigningKey,
        event_bus: EventBus,
        max_cooldown: int = 30,
        max_pool: int = 4,
        keepalive: Optional[int] = None,
    ):
        if max_pool < 2:
            raise ValueError("max_pool must be at least 2 (for event listener + query sender)")

        self._started = False
        self._transport_pool = _transport_pool_factory(
            addr, device_id, signing_key, max_pool, keepalive
        )
        self._status = BackendConnStatus.LOST
        self._status_exc = None
        self._cmds = BackendAuthenticatedCmds(addr, self._acquire_transport)
        self._manager_connect_cancel_scope = None
        self._monitors_cbs: List[Callable[..., None]] = []
        self._monitors_idle_event = trio.Event()
        self._monitors_idle_event.set()  # No monitors
        self._backend_connection_failures = 0
        self.event_bus = event_bus
        self.max_cooldown = max_cooldown

    @property
    def status(self) -> BackendConnStatus:
        return self._status

    @property
    def status_exc(self) -> Optional[Exception]:
        return self._status_exc

    @property
    def cmds(self) -> BackendAuthenticatedCmds:
        return self._cmds

    def register_monitor(self, monitor_cb) -> None:
        if self._started:
            raise RuntimeError("Cannot register monitor once started !")
        self._monitors_cbs.append(monitor_cb)

    def are_monitors_idle(self):
        return self._monitors_idle_event.is_set()

    async def wait_idle_monitors(self):
        await self._monitors_idle_event.wait()

    @asynccontextmanager
    async def run(self):
        if self._started:
            raise RuntimeError("Already started")
        async with trio.open_service_nursery() as nursery:
            nursery.start_soon(self._run_manager)
            yield
            nursery.cancel_scope.cancel()

    async def _run_manager(self):
        while True:
            try:
                with trio.CancelScope() as self._manager_connect_cancel_scope:
                    try:
                        await self._manager_connect()
                    except (BackendNotAvailable, BackendConnectionRefused):
                        pass
                    except Exception as exc:
                        self._status = BackendConnStatus.CRASHED
                        self._status_exc = BackendNotAvailable(
                            f"Backend connection manager has crashed: {exc}"
                        )
                        logger.exception("Unhandled exception")
            finally:
                self._manager_connect_cancel_scope = None

            assert self._status not in (BackendConnStatus.READY, BackendConnStatus.INITIALIZING)
            if self._backend_connection_failures == 0:
                self.event_bus.send(
                    CoreEvent.BACKEND_CONNECTION_CHANGED,
                    status=self._status,
                    status_exc=self._status_exc,
                )
            if self._status == BackendConnStatus.LOST:
                # Start with a 1s cooldown and increase by power of 2 until
                # max cooldown every time the connection trial fails
                # (e.g. 1, 2, 4, 8, 15, 15, 15 etc. if max cooldown is 15s)
                cooldown_time = 2 ** self._backend_connection_failures
                if cooldown_time > self.max_cooldown:
                    cooldown_time = self.max_cooldown
                self._backend_connection_failures += 1
                logger.info("Backend offline", cooldown_time=cooldown_time)
                await trio.sleep(cooldown_time)
            else:
                # It's most likely useless to retry connection anyway
                logger.info("Backend connection refused", status=self._status)
                await trio.sleep_forever()

    def _cancel_manager_connect(self):
        if self._manager_connect_cancel_scope:
            self._manager_connect_cancel_scope.cancel()

    async def _manager_connect(self):
        async with self._acquire_transport(ignore_status=True, force_fresh=True) as transport:
            self._status = BackendConnStatus.INITIALIZING
            self._status_exc = None
            self._backend_connection_failures = 0
            self.event_bus.send(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                status=self._status,
                status_exc=self._status_exc,
            )
            logger.info("Backend online")

            await cmds.events_subscribe(transport)

            # Quis custodiet ipsos custodes?
            monitors_states = ["STALLED" for _ in range(len(self._monitors_cbs))]

            async def _wrap_monitor_cb(monitor_cb, idx, *, task_status=trio.TASK_STATUS_IGNORED):
                def _idle():
                    monitors_states[idx] = "IDLE"
                    if all(state == "IDLE" for state in monitors_states):
                        self._monitors_idle_event.set()

                def _awake():
                    monitors_states[idx] = "AWAKE"
                    if self._monitors_idle_event.is_set():
                        self._monitors_idle_event = trio.Event()

                task_status.idle = _idle
                task_status.awake = _awake
                await monitor_cb(task_status=task_status)

            try:
                async with trio.open_service_nursery() as monitors_nursery:

                    async with trio.open_service_nursery() as monitors_bootstrap_nursery:
                        for idx, monitor_cb in enumerate(self._monitors_cbs):
                            monitors_bootstrap_nursery.start_soon(
                                monitors_nursery.start, partial(_wrap_monitor_cb, monitor_cb, idx)
                            )

                    self._status = BackendConnStatus.READY
                    self.event_bus.send(
                        CoreEvent.BACKEND_CONNECTION_CHANGED, status=self._status, status_exc=None
                    )

                    while True:
                        rep = await cmds.events_listen(transport, wait=True)
                        _handle_event(self.event_bus, rep)

            finally:
                # No more monitors are running
                self._monitors_idle_event.set()

    @asynccontextmanager
    async def _acquire_transport(
        self, force_fresh=False, ignore_status=False, allow_not_available=False
    ):
        if not ignore_status:
            if self._status_exc:
                raise self._status_exc

        try:
            async with self._transport_pool.acquire(force_fresh=force_fresh) as transport:
                yield transport

        except BackendNotAvailable as exc:
            if not allow_not_available:
                self._status = BackendConnStatus.LOST
                self._status_exc = exc
                self._cancel_manager_connect()
            raise

        except BackendConnectionRefused as exc:
            self._status = BackendConnStatus.REFUSED
            self._status_exc = exc
            self._cancel_manager_connect()
            raise


@asynccontextmanager
async def backend_authenticated_cmds_factory(
    addr: BackendOrganizationAddr,
    device_id: DeviceID,
    signing_key: SigningKey,
    keepalive: Optional[int] = None,
) -> AsyncGenerator[BackendAuthenticatedCmds, None]:
    """
    Raises:
        BackendConnectionError
    """
    transport_lock = trio.Lock()
    transport = None
    closed = False

    async def _init_transport():
        nonlocal transport
        if not transport:
            if closed:
                raise trio.ClosedResourceError
            transport = await connect_as_authenticated(
                addr, device_id=device_id, signing_key=signing_key, keepalive=keepalive
            )
            transport.logger = transport.logger.bind(device_id=device_id)

    async def _destroy_transport():
        nonlocal transport
        if transport:
            await transport.aclose()
            transport = None

    @asynccontextmanager
    async def _acquire_transport(**kwargs):
        nonlocal transport

        async with transport_lock:
            await _init_transport()
            try:
                yield transport
            except BackendNotAvailable:
                await _destroy_transport()
                raise

    try:
        yield BackendAuthenticatedCmds(addr, _acquire_transport)

    finally:
        async with transport_lock:
            closed = True
            await _destroy_transport()
