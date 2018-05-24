import os
import pytest
import attr
import socket
import blinker
import contextlib
from unittest.mock import patch

from parsec.core.backend_connections_multiplexer import BackendConnectionsMultiplexer
from parsec.core.local_storage import LocalStorage
from parsec.core.devices_manager import Device
from parsec.backend.exceptions import AlreadyExistsError as UserAlreadyExistsError

from tests.common import (
    AsyncMock,
    freeze_time,
    run_app,
    backend_factory,
    core_factory,
    connect_backend,
    connect_core,
)
from tests.open_tcp_stream_mock_wrapper import OpenTCPStreamMockWrapper


def pytest_addoption(parser):
    parser.addoption("--hypothesis-max-examples", default=100, type=int)
    parser.addoption("--hypothesis-derandomize", action="store_true")
    parser.addoption(
        "--no-postgresql", action="store_true", help="Don't run tests making use of PostgreSQL"
    )
    parser.addoption(
        "--only-postgresql", action="store_true", help="Only run tests making use of PostgreSQL"
    )
    parser.addoption("--runslow", action="store_true", help="Don't skip slow tests")


def pytest_runtest_setup(item):
    if "slow" in item.keywords and not item.config.getoption("--runslow"):
        pytest.skip("need --runslow option to run")


# Use current unix user's credential, don't forget to do
# `psql -c 'CREATE DATABASE parsec_test;'` prior to run tests
DEFAULT_POSTGRESQL_TEST_URL = "postgresql:///parsec_test"


def postgresql_url():
    return os.environ.get("PARSEC_POSTGRESQL_TEST_URL", DEFAULT_POSTGRESQL_TEST_URL)


@pytest.fixture(params=["mocked", "postgresql"])
def backend_store(request):
    if request.param == "postgresql":
        if pytest.config.getoption("--no-postgresql"):
            pytest.skip("`--no-postgresql` option provided")
        pg_driver = pytest.importorskip("parsec.backend.drivers.postgresql")
        url = postgresql_url()
        conn = pg_driver.handler.init_db(url, force=True)
        conn.close()
        return url

    else:
        if pytest.config.getoption("--only-postgresql"):
            pytest.skip("`--only-postgresql` option provided")
        return "mocked://"


@pytest.fixture
def alice(tmpdir):
    return Device(
        "alice@test",
        (
            b"\xceZ\x9f\xe4\x9a\x19w\xbc\x12\xc8\x98\xd1CB\x02vS\xa4\xfe\xc8\xc5"
            b"\xa6\xcd\x87\x90\xd7\xabJ\x1f$\x87\xc4"
        ),
        (
            b"\xa7\n\xb2\x94\xbb\xe6\x03\xd3\xd0\xd3\xce\x95\xe6\x8b\xfe5`("
            b"\x15\xc0UL\xe9\x1dTf^ m\xb7\xbc\\"
        ),
        tmpdir.join("alice@test.sqlite").strpath,
    )


@pytest.fixture
def alice2(tmpdir):
    return Device(
        "alice@otherdevice",
        (
            b"\xceZ\x9f\xe4\x9a\x19w\xbc\x12\xc8\x98\xd1CB\x02vS\xa4\xfe\xc8\xc5"
            b"\xa6\xcd\x87\x90\xd7\xabJ\x1f$\x87\xc4"
        ),
        (b"s\x9cA\xb0|\xa4\x1a84z\xfe\xbe\x16\xc0y1.\x05Z\xe2#\x9em>WQ\xd0\x82y\t\x94\x8b"),
        tmpdir.join("alice@otherdevice.sqlite").strpath,
    )


@pytest.fixture
def bob(tmpdir):
    return Device(
        "bob@test",
        (
            b"\xc3\xc9(\xf7\\\xd2\xb4[\x85\xe5\xfa\xd3\xad\xbc9\xc6Y\xa3%G{\x08ks"
            b"\xc5\xff\xb3\x97\xf6\xdf\x8b\x0f"
        ),
        (
            b"!\x94\x93\xda\x0cC\xc6\xeb\x80\xbc$\x8f\xaf\xeb\x83\xcb`T\xcf"
            b"\x96R\x97{\xd5Nx\x0c\x04\xe96a\xb0"
        ),
        tmpdir.join("bob@test.sqlite").strpath,
    )


@pytest.fixture
def mallory(tmpdir):
    return Device(
        "mallory@test",
        (
            b"sD\xae\x91^\xae\xcc\xe7.\x89\xc8\x91\x9f\xa0t>B\x93\x07\xe7\xb5"
            b"\xb0\x81\xb1\x07\xf0\xe5\x9b\x91\xd0`:"
        ),
        (
            b"\xcd \x7f\xf5\x91\x17=\xda\x856Sz\xe0\xf9\xc6\x82!O7g9\x01`s\xdd"
            b"\xeeoj\xcb\xe7\x0e\xc5"
        ),
        tmpdir.join("mallory@test.sqlite").strpath,
    )


@pytest.fixture
def always_logs():
    """
    By default, pytest-logbook only print last test's logs in case of error.
    With this fixture all logs are outputed as soon as they are created.
    """
    from logbook import StreamHandler
    import sys

    sh = StreamHandler(sys.stdout)
    with sh.applicationbound():
        yield


@pytest.fixture
def unused_tcp_port():
    """Find an unused localhost TCP port from 1024-65535 and return it."""
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def unused_tcp_addr(unused_tcp_port):
    return "tcp://127.0.0.1:%s" % unused_tcp_port


@pytest.fixture
def signal_ns():
    return blinker.Namespace()


@pytest.fixture
def default_devices(alice, alice2, bob):
    return (alice, alice2, bob)


@pytest.fixture
async def backend(nursery, default_devices, backend_store, config={}):
    async with backend_factory(
        **{"blockstore_postgresql": True, "dburl": backend_store, **config}
    ) as backend:

        with freeze_time("2000-01-01"):
            for device in default_devices:
                try:
                    await backend.user.create(
                        author="<backend-fixture>",
                        user_id=device.user_id,
                        broadcast_key=device.user_pubkey.encode(),
                        devices=[(device.device_name, device.device_verifykey.encode())],
                    )
                except UserAlreadyExistsError:
                    await backend.user.create_device(
                        user_id=device.user_id,
                        device_name=device.device_name,
                        verify_key=device.device_verifykey.encode(),
                    )

        yield backend


@pytest.fixture
def backend_addr(tcp_stream_spy):
    return "tcp://placeholder.com:9999"


@pytest.fixture
def tcp_stream_spy():
    open_tcp_stream_mock_wrapper = OpenTCPStreamMockWrapper()
    with patch("trio.open_tcp_stream", new=open_tcp_stream_mock_wrapper):
        yield open_tcp_stream_mock_wrapper


@attr.s(frozen=True)
class RunningBackendInfo:
    backend = attr.ib()
    addr = attr.ib()
    connection_factory = attr.ib()


@pytest.fixture
async def running_backend(tcp_stream_spy, backend, backend_addr):
    async with run_app(backend) as backend_connection_factory:
        tcp_stream_spy.install_hook(backend_addr, backend_connection_factory)

        yield RunningBackendInfo(backend, backend_addr, backend_connection_factory)

        tcp_stream_spy.install_hook(backend_addr, None)


@pytest.fixture
async def alice_backend_sock(backend, alice):
    async with connect_backend(backend, auth_as=alice) as sock:
        yield sock


@pytest.fixture
async def core(nursery, backend_addr, tmpdir, default_devices, config={}):
    async with core_factory(
        **{
            "base_settings_path": tmpdir.mkdir("core_fixture").strpath,
            "backend_addr": backend_addr,
            **config,
        }
    ) as core:

        for device in default_devices:
            core.devices_manager.register_new_device(
                device.id, device.user_privkey.encode(), device.device_signkey.encode(), "<secret>"
            )

        yield core


@pytest.fixture
async def core2(nursery, backend_addr, tmpdir, default_devices, config={}):
    # TODO: refacto with core fixture
    async with core_factory(
        **{
            "base_settings_path": tmpdir.mkdir("core2_fixture").strpath,
            "backend_addr": backend_addr,
            **config,
        }
    ) as core:

        for device in default_devices:
            core.devices_manager.register_new_device(
                device.id, device.user_privkey.encode(), device.device_signkey.encode(), "<secret>"
            )

        yield core


@pytest.fixture
async def alice_core_sock(core, alice):
    assert not core.auth_device, "Core already logged"
    async with connect_core(core) as sock:
        await core.login(alice)
        yield sock


@pytest.fixture
async def alice2_core2_sock(core2, alice2):
    assert not core2.auth_device, "Core already logged"
    async with connect_core(core2) as sock:
        await core2.login(alice2)
        yield sock


@pytest.fixture
async def bob_core2_sock(core2, bob):
    assert not core2.auth_device, "Core already logged"
    async with connect_core(core2) as sock:
        await core2.login(bob)
        yield sock


@pytest.fixture
def mocked_local_storage_connection():
    # Persistent local storage is achieve by using sqlite storing in FS.
    # However it is a lot faster to store in memory and just pass around the
    # sqlite connection object.
    import sqlite3

    class CloseProtectedConnection(sqlite3.Connection):

        def close(self):
            pass

    class InMemoryDatabaseManager:

        def __init__(self, vanilla_connect):
            self.vanilla_connect = vanilla_connect
            self.databases = {}

        def reset(self):
            # Now we can actually close the connections
            for db in self.databases.values():
                sqlite3.Connection.close(db)
            self.databases = {}

        def connect(self, database, *args, **kwargs):
            if database not in self.databases:
                connection = self.vanilla_connect(":memory:", factory=CloseProtectedConnection)
                self.databases[database] = connection
            return self.databases[database]

    manager = InMemoryDatabaseManager(sqlite3.connect)
    with patch("sqlite3.connect", new=manager.connect):
        yield manager


@pytest.fixture
def monitor():
    from tests.monitor import Monitor

    return Monitor()


@pytest.fixture
async def local_storage(nursery):
    ls = LocalStorage(":memory:")
    await ls.init(nursery)
    return ls


@pytest.fixture
def mock_bcm():
    return AsyncMock(spec=BackendConnectionsMultiplexer)
