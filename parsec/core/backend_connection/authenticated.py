# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio
from enum import Enum
from async_generator import asynccontextmanager
from typing import Optional
from structlog import get_logger

from parsec.crypto import SigningKey
from parsec.event_bus import EventBus
from parsec.api.data import EntryID
from parsec.api.protocol import DeviceID
from parsec.core.types import BackendOrganizationAddr
from parsec.core.backend_connection import cmds
from parsec.core.backend_connection.transport import connect, TransportPool
from parsec.core.backend_connection.exceptions import BackendNotAvailable, BackendConnectionRefused


logger = get_logger()


BackendConnStatus = Enum("BackendConnStatus", "READY LOST INITIALIZING REFUSED CRASHED")


class BackendAuthenticatedCmds:
    def __init__(self, addr: BackendOrganizationAddr, acquire_transport):
        self.addr = addr
        self.acquire_transport = acquire_transport

    def _expose_cmds_with_retrier(name):
        cmd = getattr(cmds, name)

        async def wrapper(self, *args, **kwargs):
            # Reusing the transports expose us to `BackendNotAvaiable` exceptions
            # due to inactivity timeout while the transport was in the pool.
            try:
                async with self.acquire_transport(allow_not_available=True) as transport:
                    return await cmd(transport, *args, **kwargs)

            except BackendNotAvailable:
                async with self.acquire_transport(force_fresh=True) as transport:
                    return await cmd(transport, *args, **kwargs)

        wrapper.__name__ = name

        return wrapper

    ping = _expose_cmds_with_retrier("ping")

    events_subscribe = _expose_cmds_with_retrier("events_subscribe")
    events_listen = _expose_cmds_with_retrier("events_listen")

    message_get = _expose_cmds_with_retrier("message_get")

    vlob_create = _expose_cmds_with_retrier("vlob_create")
    vlob_read = _expose_cmds_with_retrier("vlob_read")
    vlob_update = _expose_cmds_with_retrier("vlob_update")
    vlob_poll_changes = _expose_cmds_with_retrier("vlob_poll_changes")
    vlob_list_versions = _expose_cmds_with_retrier("vlob_list_versions")
    vlob_maintenance_get_reencryption_batch = _expose_cmds_with_retrier(
        "vlob_maintenance_get_reencryption_batch"
    )
    vlob_maintenance_save_reencryption_batch = _expose_cmds_with_retrier(
        "vlob_maintenance_save_reencryption_batch"
    )
    vlob_maintenance_get_garbage_collection_batch = _expose_cmds_with_retrier(
        "vlob_maintenance_get_garbage_collection_batch"
    )
    vlob_maintenance_save_garbage_collection_batch = _expose_cmds_with_retrier(
        "vlob_maintenance_save_garbage_collection_batch"
    )
    realm_status = _expose_cmds_with_retrier("realm_status")
    realm_create = _expose_cmds_with_retrier("realm_create")
    realm_get_role_certificates = _expose_cmds_with_retrier("realm_get_role_certificates")
    realm_update_roles = _expose_cmds_with_retrier("realm_update_roles")
    realm_start_reencryption_maintenance = _expose_cmds_with_retrier(
        "realm_start_reencryption_maintenance"
    )
    realm_finish_reencryption_maintenance = _expose_cmds_with_retrier(
        "realm_finish_reencryption_maintenance"
    )
    realm_start_garbage_collection_maintenance = _expose_cmds_with_retrier(
        "realm_start_garbage_collection_maintenance"
    )
    realm_finish_garbage_collection_maintenance = _expose_cmds_with_retrier(
        "realm_finish_garbage_collection_maintenance"
    )
    block_create = _expose_cmds_with_retrier("block_create")
    block_read = _expose_cmds_with_retrier("block_read")

    user_get = _expose_cmds_with_retrier("user_get")
    user_find = _expose_cmds_with_retrier("user_find")
    user_invite = _expose_cmds_with_retrier("user_invite")
    user_cancel_invitation = _expose_cmds_with_retrier("user_cancel_invitation")
    user_create = _expose_cmds_with_retrier("user_create")
    user_revoke = _expose_cmds_with_retrier("user_revoke")

    device_invite = _expose_cmds_with_retrier("device_invite")
    device_cancel_invitation = _expose_cmds_with_retrier("device_cancel_invitation")
    device_create = _expose_cmds_with_retrier("device_create")


def _handle_event(event_bus: EventBus, rep: dict) -> None:
    if rep["status"] != "ok":
        logger.warning("Bad response to `events_listen` command", rep=rep)
        return

    if rep["event"] == "message.received":
        event_bus.send("backend.message.received", index=rep["index"])

    elif rep["event"] == "pinged":
        event_bus.send("backend.pinged", ping=rep["ping"])

    elif rep["event"] == "realm.roles_updated":
        realm_id = EntryID(rep["realm_id"])
        event_bus.send("backend.realm.roles_updated", realm_id=realm_id, role=rep["role"])

    elif rep["event"] == "realm.vlobs_updated":
        src_id = EntryID(rep["src_id"])
        realm_id = EntryID(rep["realm_id"])
        event_bus.send(
            "backend.realm.vlobs_updated",
            realm_id=realm_id,
            checkpoint=rep["checkpoint"],
            src_id=src_id,
            src_version=rep["src_version"],
        )

    elif rep["event"] == "realm.maintenance_started":
        event_bus.send(
            "backend.realm.maintenance_started",
            realm_id=rep["realm_id"],
            encryption_revision=rep["encryption_revision"],
        )

    elif rep["event"] == "realm.maintenance_finished":
        event_bus.send(
            "backend.realm.maintenance_finished",
            realm_id=rep["realm_id"],
            encryption_revision=rep["encryption_revision"],
        )


def _transport_pool_factory(addr, device_id, signing_key, max_pool, keepalive):
    async def _connect():
        transport = await connect(
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
        self._monitors_cbs = []
        self._backend_connection_failures = 0
        self.event_bus = event_bus
        self.max_cooldown = max_cooldown

    @property
    def status(self) -> BackendConnStatus:
        return self._status

    @property
    def status_exc(self) -> Exception:
        return self._status_exc

    @property
    def cmds(self) -> BackendAuthenticatedCmds:
        return self._cmds

    def register_monitor(self, monitor_cb) -> None:
        if self._started:
            raise RuntimeError("Cannot register monitor once started !")
        self._monitors_cbs.append(monitor_cb)

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
                    "backend.connection.changed", status=self._status, status_exc=self._status_exc
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
                "backend.connection.changed", status=self._status, status_exc=self._status_exc
            )
            logger.info("Backend online")

            await cmds.events_subscribe(transport)

            async with trio.open_service_nursery() as monitors_nursery:

                # No need for a service nursery here given we dont do anything
                # async in the main coroutine
                async with trio.open_nursery() as monitors_bootstrap_nursery:

                    async def _bootstrap_monitor(monitor_cb):
                        await monitors_nursery.start(monitor_cb)

                    for monitor_cb in self._monitors_cbs:
                        monitors_bootstrap_nursery.start_soon(_bootstrap_monitor, monitor_cb)

                self._status = BackendConnStatus.READY
                self.event_bus.send(
                    "backend.connection.changed", status=self._status, status_exc=None
                )

                while True:
                    rep = await cmds.events_listen(transport, wait=True)
                    _handle_event(self.event_bus, rep)

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
async def backend_authenticated_conn_factory(
    addr: BackendOrganizationAddr,
    device_id: DeviceID,
    signing_key: SigningKey,
    event_bus: EventBus,
    max_cooldown: int = 30,
    max_pool: int = 4,
    keepalive: Optional[int] = None,
) -> BackendAuthenticatedConn:
    """
    Raises: nothing !
    """
    if max_pool < 2:
        raise ValueError("max_pool must be at least 2 (for event listener + query sender)")

    async def _connect():
        transport = await connect(
            addr, device_id=device_id, signing_key=signing_key, keepalive=keepalive
        )
        transport.logger = transport.logger.bind(device_id=device_id)
        return transport

    transport_pool = TransportPool(_connect, max_pool=max_pool)
    async with trio.open_service_nursery() as nursery:
        yield BackendAuthenticatedConn(
            nursery, transport_pool, addr=addr, event_bus=event_bus, max_cooldown=max_cooldown
        )
        nursery.cancel_scope.cancel()


@asynccontextmanager
async def backend_authenticated_cmds_factory(
    addr: BackendOrganizationAddr,
    device_id: DeviceID,
    signing_key: SigningKey,
    keepalive: Optional[int] = None,
) -> BackendAuthenticatedCmds:
    """
    Raises:
        BackendConnectionError
    """
    transport = await connect(
        addr, device_id=device_id, signing_key=signing_key, keepalive=keepalive
    )
    transport.logger = transport.logger.bind(device_id=device_id)
    transport_lock = trio.Lock()

    @asynccontextmanager
    async def _acquire_transport(**kwargs):
        async with transport_lock:
            yield transport

    try:
        yield BackendAuthenticatedCmds(addr, _acquire_transport)
    finally:
        await transport.aclose()
