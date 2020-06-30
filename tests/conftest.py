# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import contextlib
import os
import re
import socket
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import attr
import hypothesis
import pendulum
import pytest
import structlog
import trio
import trio_asyncio
from async_generator import asynccontextmanager

from parsec.backend import backend_app_factory
from parsec.backend.config import (
    BackendConfig,
    MockedBlockStoreConfig,
    PostgreSQLBlockStoreConfig,
    RAID0BlockStoreConfig,
    RAID1BlockStoreConfig,
    RAID5BlockStoreConfig,
)
from parsec.core import CoreConfig
from parsec.core.backend_connection import BackendConnStatus
from parsec.core.core_events import CoreEvent
from parsec.core.fs.storage import LocalDatabase, UserStorage, local_database
from parsec.core.logged_core import logged_core_factory
from parsec.core.mountpoint.manager import get_mountpoint_runner
from parsec.core.types import BackendAddr
from parsec.monitoring import TaskMonitoringInstrument
from tests.common import addr_with_device_subdomain, freeze_time
from tests.event_bus_spy import SpiedEventBus
from tests.fixtures import *  # noqa
from tests.open_tcp_stream_mock_wrapper import OpenTCPStreamMockWrapper, offline
from tests.oracles import oracle_fs_factory, oracle_fs_with_sync_factory  # noqa: Republishing
from tests.postgresql import (
    asyncio_reset_postgresql_testbed,
    bootstrap_postgresql_testbed,
    get_postgresql_url,
    reset_postgresql_testbed,
)

# TODO: needed ?
pytest.register_assert_rewrite("tests.event_bus_spy")


def pytest_addoption(parser):
    parser.addoption("--hypothesis-max-examples", default=100, type=int)
    parser.addoption("--hypothesis-derandomize", action="store_true")
    parser.addoption(
        "--postgresql",
        action="store_true",
        help=(
            "Use PostgreSQL backend instead of default memory mock "
            "(use `PG_URL` env var to customize the database to use)"
        ),
    )
    parser.addoption("--runslow", action="store_true", help="Don't skip slow tests")
    parser.addoption("--runmountpoint", action="store_true", help="Don't skip FUSE/WinFSP tests")
    parser.addoption("--rungui", action="store_true", help="Don't skip GUI tests")
    parser.addoption(
        "--realcrypto", action="store_true", help="Don't mock crypto operation to save time"
    )
    parser.addoption(
        "--run-postgresql-cluster",
        action="store_true",
        help=(
            "Instead of running the tests, only start a PostgreSQL cluster "
            "that could be use for other tests (through `PG_URL` env var) "
            "to avoid having to create a new cluster each time."
        ),
    )


def is_xdist_master(config):
    return config.getoption("dist") != "no" and not os.environ.get("PYTEST_XDIST_WORKER")


@pytest.fixture(scope="session", autouse=True)
def mock_timezone_utc(request):
    # Mock and non-UTC timezones are a really bad mix, so keep things simple
    with pendulum.tz.LocalTimezone.test(pendulum.timezone("utc")):
        yield


def pytest_configure(config):
    # Patch pytest-trio
    patch_pytest_trio()
    # Configure structlog to redirect everything in logging
    structlog.configure(
        logger_factory=structlog.stdlib.LoggerFactory(),
        processors=[
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )
    # Lock configuration
    structlog.configure = lambda *args, **kwargs: None
    # Add helper to caplog
    patch_caplog()
    if config.getoption("--run-postgresql-cluster"):
        pgurl = bootstrap_postgresql_testbed()
        capturemanager = config.pluginmanager.getplugin("capturemanager")
        if capturemanager:
            capturemanager.suspend(in_=True)
        print(f"usage: PG_URL={pgurl} py.test --postgresql tests")
        input("Press enter when you're done with...")
        pytest.exit("bye")
    elif config.getoption("--postgresql") and not is_xdist_master(config):
        bootstrap_postgresql_testbed()


def patch_caplog():
    from _pytest.logging import LogCaptureFixture

    def _remove_colors(msg):
        return re.sub(r"\x1B\[([0-9]{1,2}(;[0-9]{1,2})?)?[mGK]", "", str(msg))

    def _assert_occured(self, log):
        __tracebackhide__ = True
        assert any([r for r in self.records if log in _remove_colors(r.msg)])

    LogCaptureFixture.assert_occured = _assert_occured


def patch_pytest_trio():
    # Fix while waiting for
    # https://github.com/python-trio/pytest-trio/issues/77
    import pytest_trio

    vanilla_crash = pytest_trio.plugin.TrioTestContext.crash

    def patched_crash(self, exc):
        if exc is None:
            task = trio.hazmat.current_task()
            for child_nursery in task.child_nurseries:
                for child_exc in child_nursery._pending_excs:
                    if not isinstance(exc, trio.Cancelled):
                        vanilla_crash(self, child_exc)
        vanilla_crash(self, exc)

    pytest_trio.plugin.TrioTestContext.crash = patched_crash

    def fget(self):
        if self.crashed and not self._error_list:
            self._error_list.append(trio.TrioInternalError("See pytest-trio issue #75"))
        return self._error_list

    def fset(self, value):
        self._error_list = value

    error_list = property(fget, fset)
    pytest_trio.plugin.TrioTestContext.error_list = error_list


@pytest.fixture(scope="session")
def hypothesis_settings(request):
    return hypothesis.settings(
        max_examples=request.config.getoption("--hypothesis-max-examples"),
        derandomize=request.config.getoption("--hypothesis-derandomize"),
        deadline=None,
    )


def pytest_runtest_setup(item):
    if item.get_closest_marker("slow") and not item.config.getoption("--runslow"):
        pytest.skip("need --runslow option to run")
    if item.get_closest_marker("win32") and sys.platform != "win32":
        pytest.skip("test specific to win32")
    if item.get_closest_marker("linux") and sys.platform != "linux":
        pytest.skip("test specific to linux")
    if item.get_closest_marker("mountpoint"):
        if not item.config.getoption("--runmountpoint"):
            pytest.skip("need --runmountpoint option to run")
        elif not get_mountpoint_runner():
            pytest.skip("FUSE/WinFSP not available")
    if item.get_closest_marker("gui"):
        if not item.config.getoption("--rungui"):
            pytest.skip("need --rungui option to run")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "trio" in item.keywords:
            item.fixturenames.append("task_monitoring")


@pytest.fixture
async def task_monitoring():
    trio.hazmat.add_instrument(TaskMonitoringInstrument())


@pytest.fixture(autouse=True, scope="session", name="unmock_crypto")
def mock_crypto(request):
    # Crypto is CPU hungry
    if request.config.getoption("--realcrypto"):

        def unmock():
            pass

        yield unmock

    else:

        def unsecure_but_fast_argon2i_kdf(size, password, salt, *args, **kwargs):
            data = password + salt
            return data[:size] + b"\x00" * (size - len(data))

        from parsec.crypto import argon2i

        vanilla_kdf = argon2i.kdf

        def unmock():
            return patch("parsec.crypto.argon2i.kdf", new=vanilla_kdf)

        with patch("parsec.crypto.argon2i.kdf", new=unsecure_but_fast_argon2i_kdf):
            yield unmock


@pytest.fixture
def realcrypto(unmock_crypto):
    with unmock_crypto():
        yield


@pytest.fixture(scope="session")
def postgresql_url(request):
    if not request.config.getoption("--postgresql"):
        pytest.skip("`--postgresql` option not provided")
    return get_postgresql_url()


@pytest.fixture
async def asyncio_loop(request):
    # asyncio loop is only needed for triopg
    if not request.config.getoption("--postgresql"):
        yield None

    else:
        # When a ^C happens, trio send a Cancelled exception to each running
        # coroutine. We must protect this one to avoid deadlock if it is cancelled
        # before another coroutine that uses trio-asyncio.
        with trio.CancelScope(shield=True):
            async with trio_asyncio.open_loop() as loop:
                yield loop


@pytest.fixture(scope="session")
def unused_tcp_port():
    """Find an unused localhost TCP port from 1024-65535 and return it."""
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def event_bus_factory():
    return SpiedEventBus


@pytest.fixture
def event_bus(event_bus_factory):
    return event_bus_factory()


@pytest.fixture
def tcp_stream_spy():
    open_tcp_stream_mock_wrapper = OpenTCPStreamMockWrapper()
    with patch("trio.open_tcp_stream", new=open_tcp_stream_mock_wrapper):
        yield open_tcp_stream_mock_wrapper


@pytest.fixture(scope="session")
def monitor():
    from tests.monitor import Monitor

    return Monitor()


@attr.s
class AppServer:
    entry_point = attr.ib()
    addr = attr.ib()
    connection_factory = attr.ib()

    def offline(self):
        return offline(self.addr)


@pytest.fixture
def server_factory(tcp_stream_spy):
    count = 0

    @asynccontextmanager
    async def _server_factory(entry_point, addr=None):
        nonlocal count
        count += 1

        if not addr:
            addr = BackendAddr(hostname=f"server-{count}.localhost", port=9999, use_ssl=False)

        async with trio.open_service_nursery() as nursery:

            def connection_factory(*args, **kwargs):
                client_stream, server_stream = trio.testing.memory_stream_pair()
                nursery.start_soon(entry_point, server_stream)
                return client_stream

            tcp_stream_spy.push_hook(addr, connection_factory)
            try:
                yield AppServer(entry_point, addr, connection_factory)
                nursery.cancel_scope.cancel()

            finally:
                # It's important to remove the hook just after having cancelled
                # the nursery. Otherwise another coroutine trying to connect would
                # end up with a `RuntimeError('Nursery is closed to new arrivals',)`
                # given `connection_factory` make use of the now-closed nursery.
                tcp_stream_spy.pop_hook(addr)

    return _server_factory


@pytest.fixture()
def backend_addr(tcp_stream_spy):
    # Depending on tcp_stream_spy fixture prevent from doing real connection
    # attempt (which can be long to resolve) when backend is not running
    return BackendAddr(hostname="example.com", port=9999, use_ssl=False)


@pytest.fixture
def persistent_mockup(monkeypatch):
    @attr.s
    class MockupContext:

        connections = attr.ib(factory=dict)

        def get(self, path):
            if path not in self.connections:
                self.connections[path] = sqlite3.connect(":memory:", check_same_thread=False)
            return self.connections[path]

        def clear(self):
            for connection in self.connections.values():
                try:
                    connection.close()
                except sqlite3.ProgrammingError:
                    # Connections will raise error if they were opened from another
                    # thread. This only occurs for a couple of tests so no big deal.
                    pass
            self.connections.clear()

    mockup_context = MockupContext()
    storage_set = set()

    async def _create_connection(storage):
        storage_set.add(storage)
        return mockup_context.get(storage.path)

    async def _close(storage):
        # Idempotent operation
        storage_set.discard(storage)
        storage._conn = None

    @asynccontextmanager
    async def thread_pool_runner(max_workers):
        assert max_workers == 1

        async def run_in_thread(fn, *args):
            return fn(*args)

        yield run_in_thread

    monkeypatch.setattr(local_database, "thread_pool_runner", thread_pool_runner)
    monkeypatch.setattr(LocalDatabase, "_create_connection", _create_connection)
    monkeypatch.setattr(LocalDatabase, "_close", _close)

    yield mockup_context
    mockup_context.clear()
    assert not storage_set


@pytest.fixture
def reset_testbed(request, caplog, persistent_mockup):
    async def _reset_testbed(keep_logs=False):
        if request.config.getoption("--postgresql"):
            await trio_asyncio.aio_as_trio(asyncio_reset_postgresql_testbed)
        persistent_mockup.clear()
        if not keep_logs:
            caplog.clear()

    return _reset_testbed


@pytest.fixture()
def backend_store(request):
    if request.config.getoption("--postgresql"):
        reset_postgresql_testbed()
        return get_postgresql_url()

    elif request.node.get_closest_marker("postgresql"):
        pytest.skip("`Test is postgresql-only")

    else:
        return "MOCKED"


@pytest.fixture
def blockstore(request, backend_store):
    # TODO: allow to test against swift ?
    if backend_store.startswith("postgresql://"):
        config = PostgreSQLBlockStoreConfig()
    else:
        config = MockedBlockStoreConfig()

    # More or less a hack to be able to to configure this fixture from
    # the test function by adding tags to it
    if request.node.get_closest_marker("raid0_blockstore"):
        config = RAID0BlockStoreConfig(blockstores=[config, MockedBlockStoreConfig()])
    if request.node.get_closest_marker("raid1_blockstore"):
        config = RAID1BlockStoreConfig(blockstores=[config, MockedBlockStoreConfig()])
    if request.node.get_closest_marker("raid5_blockstore"):
        config = RAID5BlockStoreConfig(
            blockstores=[config, MockedBlockStoreConfig(), MockedBlockStoreConfig()]
        )

    return config


@pytest.fixture
async def nursery():
    # A word about the nursery fixture:
    # The whole point of trio is to be able to build a graph of coroutines to
    # simplify teardown. Using a single top level nursery kind of mitigate this
    # given unrelated coroutines will end up there and be closed all together.
    # Worst, among those coroutine it could exists a relationship that will be lost
    # in a more or less subtle way (typically using a factory fixture that use the
    # default nursery behind the scene).
    # Bonus points occur if using trio-asyncio that creates yet another hidden
    # layer of relationship that could end up in cryptic dead lock hardened enough
    # to survive ^C.
    # Finally if your still no convinced, factory fixtures not depending on async
    # fixtures (like nursery is) can be used inside the Hypothesis tests.
    # I know you love Hypothesis. Checkmate. You won't use this fixture ;-)
    raise RuntimeError("Bad kitty ! Bad !!!")


@pytest.fixture
def backend_factory(
    asyncio_loop,
    event_bus_factory,
    backend_data_binder_factory,
    coolorg,
    expiredorg,
    otherorg,
    alice,
    alice2,
    expiredorgalice,
    otheralice,
    adam,
    bob,
    initial_user_manifest_state,
    blockstore,
    backend_store,
):
    # Given the postgresql driver uses trio-asyncio, any coroutine dealing with
    # the backend should inherit from the one with the asyncio loop context manager.
    # This mean the nursery fixture cannot use the backend object otherwise we
    # can end up in a dead lock if the asyncio loop is torndown before the
    # nursery fixture is done with calling the backend's postgresql stuff.

    @asynccontextmanager
    async def _backend_factory(populated=True, config={}, event_bus=None):
        config = BackendConfig(
            **{
                "administration_token": "s3cr3t",
                "db_drop_deleted_data": False,
                "db_min_connections": 1,
                "db_max_connections": 5,
                "debug": False,
                "db_url": backend_store,
                "blockstore_config": blockstore,
                **config,
            }
        )
        if not event_bus:
            event_bus = event_bus_factory()
        # TODO: backend connection to postgresql will timeout if we use a trio
        # mock clock with autothreshold. We should detect this and do something here...
        async with backend_app_factory(config, event_bus=event_bus) as backend:
            if populated:
                with freeze_time("2000-01-01"):
                    binder = backend_data_binder_factory(backend)
                    await binder.bind_organization(coolorg, alice)
                    await binder.bind_organization(
                        expiredorg, expiredorgalice, expiration_date=pendulum.now()
                    )
                    await binder.bind_organization(otherorg, otheralice)
                    await binder.bind_device(alice2, certifier=alice)
                    await binder.bind_device(adam, certifier=alice2)
                    await binder.bind_device(bob, certifier=adam)

            yield backend

    return _backend_factory


@pytest.fixture
async def backend(backend_factory, request):
    populate = "backend_not_populated" not in request.keywords
    async with backend_factory(populated=populate) as backend:
        yield backend


@pytest.fixture
def backend_data_binder(backend, backend_data_binder_factory):
    return backend_data_binder_factory(backend)


@pytest.fixture
def running_backend_ready(request):
    # Useful to synchronize other fixtures that need to connect to
    # the backend if it is available
    event = trio.Event()
    # Nothing to wait if current test doesn't use `running_backend` fixture
    if "running_backend" not in request.fixturenames:
        event.set()

    return event


@pytest.fixture
async def running_backend(server_factory, backend_addr, backend, running_backend_ready):

    async with server_factory(backend.handle_client, backend_addr) as server:
        server.backend = backend

        def _offline_for(device_id):
            return offline(addr_with_device_subdomain(server.addr, device_id))

        server.offline_for = _offline_for

        running_backend_ready.set()
        yield server


@pytest.fixture
def core_config(tmpdir):
    tmpdir = Path(tmpdir)
    return CoreConfig(
        config_dir=tmpdir / "config",
        cache_base_dir=tmpdir / "cache",
        data_base_dir=tmpdir / "data",
        mountpoint_base_dir=tmpdir / "mnt",
    )


@pytest.fixture
def core_factory(
    request, running_backend_ready, event_bus_factory, core_config, initialize_userfs_storage_v1
):
    @asynccontextmanager
    async def _core_factory(device, event_bus=None, user_manifest_in_v0=False):
        await running_backend_ready.wait()
        event_bus = event_bus or event_bus_factory()

        if not user_manifest_in_v0:
            # Create a storage just for this operation (the underlying database
            # will be reused by the core's storage thanks to `persistent_mockup`)
            path = core_config.data_base_dir / device.slug
            async with UserStorage.run(device=device, path=path) as storage:
                await initialize_userfs_storage_v1(storage)

        with event_bus.listen() as spy:
            async with logged_core_factory(core_config, device, event_bus) as core:
                # On startup core is always considered offline.
                # Hence we risk concurrency issues if the connection to backend
                # switches online concurrently with the test.
                if "running_backend" in request.fixturenames:
                    await spy.wait_with_timeout(
                        CoreEvent.BACKEND_CONNECTION_CHANGED,
                        {"status": BackendConnStatus.READY, "status_exc": spy.ANY},
                    )
                assert core.are_monitors_idle()

                yield core

    return _core_factory


@pytest.fixture
async def alice_core(core_factory, alice):
    async with core_factory(alice) as core:
        yield core


@pytest.fixture
async def alice2_core(core_factory, alice2):
    async with core_factory(alice2) as core:
        yield core


@pytest.fixture
async def adam_core(core_factory, adam):
    async with core_factory(adam) as core:
        yield core


@pytest.fixture
async def bob_core(core_factory, bob):
    async with core_factory(bob) as core:
        yield core
