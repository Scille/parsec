# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio

from parsec.api.protocol import RealmRole
from parsec.backend.backend_events import BackendEvent
from parsec.core.backend_connection import (
    BackendAuthenticatedConn,
    BackendConnectionRefused,
    BackendConnStatus,
    BackendNotAvailable,
)
from parsec.core.core_events import CoreEvent


@pytest.fixture
async def alice_backend_conn(alice, event_bus_factory, running_backend_ready):
    await running_backend_ready.wait()
    event_bus = event_bus_factory()
    conn = BackendAuthenticatedConn(
        alice.organization_addr, alice.device_id, alice.signing_key, event_bus
    )
    with event_bus.listen() as spy:
        async with conn.run():
            await spy.wait_with_timeout(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                {"status": BackendConnStatus.READY, "status_exc": None},
            )
            yield conn


@pytest.mark.trio
async def test_init_with_backend_online(running_backend, event_bus, alice):
    monitor_initialized = False
    monitor_teardown = False

    async def _monitor(*, task_status=trio.TASK_STATUS_IGNORED):
        nonlocal monitor_initialized, monitor_teardown
        monitor_initialized = True
        try:
            task_status.started()
            await trio.sleep_forever()
        finally:
            monitor_teardown = True

    conn = BackendAuthenticatedConn(
        alice.organization_addr, alice.device_id, alice.signing_key, event_bus
    )
    assert conn.status == BackendConnStatus.LOST
    conn.register_monitor(_monitor)
    with event_bus.listen() as spy:
        async with conn.run():
            await spy.wait_multiple_with_timeout(
                [
                    (
                        CoreEvent.BACKEND_CONNECTION_CHANGED,
                        {"status": BackendConnStatus.INITIALIZING, "status_exc": None},
                    ),
                    (
                        CoreEvent.BACKEND_CONNECTION_CHANGED,
                        {"status": BackendConnStatus.READY, "status_exc": None},
                    ),
                ]
            )
            assert conn.status == BackendConnStatus.READY
            assert monitor_initialized
            assert not monitor_teardown

            # Test command
            rep = await conn.cmds.ping("foo")
            assert rep == {"status": "ok", "pong": "foo"}

            # Test events
            running_backend.backend.event_bus.send(
                BackendEvent.PINGED,
                organization_id=alice.organization_id,
                author="bob@test",
                ping="foo",
            )
            await spy.wait_with_timeout(CoreEvent.BACKEND_PINGED, {"ping": "foo"})

        assert monitor_teardown


@pytest.mark.trio
async def test_init_with_backend_offline(event_bus, alice):
    conn = BackendAuthenticatedConn(
        alice.organization_addr, alice.device_id, alice.signing_key, event_bus
    )
    assert conn.status == BackendConnStatus.LOST
    with event_bus.listen() as spy:
        async with conn.run():
            await spy.wait_with_timeout(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                {"status": BackendConnStatus.LOST, "status_exc": spy.ANY},
            )
            assert conn.status == BackendConnStatus.LOST

            # Test command not possible
            with pytest.raises(BackendNotAvailable):
                await conn.cmds.ping("foo")


@pytest.mark.trio
@pytest.mark.parametrize("during_bootstrap", (True, False))
async def test_monitor_crash(running_backend, event_bus, alice, during_bootstrap):
    async def _bad_monitor(*, task_status=trio.TASK_STATUS_IGNORED):
        if during_bootstrap:
            raise RuntimeError("D'oh !")
        task_status.started()
        await trio.sleep(0)
        raise RuntimeError("D'oh !")

    conn = BackendAuthenticatedConn(
        alice.organization_addr, alice.device_id, alice.signing_key, event_bus
    )
    with event_bus.listen() as spy:
        conn.register_monitor(_bad_monitor)
        async with conn.run():
            await spy.wait_with_timeout(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                {"status": BackendConnStatus.CRASHED, "status_exc": spy.ANY},
            )
            assert conn.status == BackendConnStatus.CRASHED

            # Test command not possible
            with pytest.raises(BackendNotAvailable) as exc:
                await conn.cmds.ping()
            assert str(exc.value) == "Backend connection manager has crashed: D'oh !"


@pytest.mark.trio
async def test_switch_offline(mock_clock, running_backend, event_bus, alice):
    mock_clock.rate = 1.0
    conn = BackendAuthenticatedConn(
        alice.organization_addr, alice.device_id, alice.signing_key, event_bus
    )
    with event_bus.listen() as spy:
        async with conn.run():

            # Wait for the connection to be initialized
            await spy.wait_with_timeout(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                {"status": BackendConnStatus.READY, "status_exc": None},
            )

            # Switch backend offline and wait for according event
            spy.clear()
            with running_backend.offline():
                await spy.wait_with_timeout(
                    CoreEvent.BACKEND_CONNECTION_CHANGED,
                    {"status": BackendConnStatus.LOST, "status_exc": spy.ANY},
                )
                assert conn.status == BackendConnStatus.LOST

            # Here backend switch back online, wait for the corresponding event
            spy.clear()

            # Backend event manager waits before retrying to connect
            mock_clock.jump(5.0)
            await spy.wait_with_timeout(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                {"status": BackendConnStatus.READY, "status_exc": None},
            )
            assert conn.status == BackendConnStatus.READY

            # Make sure event system still works as expected
            spy.clear()
            running_backend.backend.event_bus.send(
                BackendEvent.PINGED,
                organization_id=alice.organization_id,
                author="bob@test",
                ping="foo",
            )
            await spy.wait_with_timeout(CoreEvent.BACKEND_PINGED, {"ping": "foo"})


@pytest.mark.trio
async def test_concurrency_sends(running_backend, alice, event_bus):
    CONCURRENCY = 10
    work_done_counter = 0
    work_all_done = trio.Event()

    async def sender(cmds, x):
        nonlocal work_done_counter
        rep = await cmds.ping(x)
        assert rep == {"status": "ok", "pong": str(x)}
        work_done_counter += 1
        if work_done_counter == CONCURRENCY:
            work_all_done.set()

    conn = BackendAuthenticatedConn(
        alice.organization_addr,
        alice.device_id,
        alice.signing_key,
        event_bus,
        max_pool=CONCURRENCY // 2,
    )
    async with conn.run():

        async with trio.open_service_nursery() as nursery:
            for x in range(CONCURRENCY):
                nursery.start_soon(sender, conn.cmds, str(x))

        with trio.fail_after(1):
            await work_all_done.wait()


@pytest.mark.trio
async def test_realm_notif_on_new_entry_sync(running_backend, alice_backend_conn, alice2_user_fs):
    wid = await alice2_user_fs.workspace_create("foo")
    workspace = alice2_user_fs.get_workspace(wid)

    await workspace.touch("/foo")
    entry_id = await workspace.path_id("/foo")

    with alice_backend_conn.event_bus.listen() as spy:
        await workspace.sync()
        await spy.wait_multiple_with_timeout(
            [
                # File manifest creation
                (
                    CoreEvent.BACKEND_REALM_VLOBS_UPDATED,
                    {"realm_id": wid, "checkpoint": 1, "src_id": entry_id, "src_version": 1},
                ),
                # Workspace manifest creation containing the file entry
                (
                    CoreEvent.BACKEND_REALM_VLOBS_UPDATED,
                    {"realm_id": wid, "checkpoint": 2, "src_id": wid, "src_version": 1},
                ),
            ]
        )


@pytest.mark.trio
async def test_realm_notif_on_new_workspace_sync(
    running_backend, alice_backend_conn, alice2_user_fs
):
    uid = alice2_user_fs.user_manifest_id
    wid = await alice2_user_fs.workspace_create("foo")

    with alice_backend_conn.event_bus.listen() as spy:
        await alice2_user_fs.sync()
        await spy.wait_multiple_with_timeout(
            [
                # Access to newly created realm
                (CoreEvent.BACKEND_REALM_ROLES_UPDATED, {"realm_id": wid, "role": RealmRole.OWNER}),
                # New realm workspace manifest created
                (
                    CoreEvent.BACKEND_REALM_VLOBS_UPDATED,
                    {"realm_id": wid, "checkpoint": 1, "src_id": wid, "src_version": 1},
                ),
                # User manifest updated
                (
                    CoreEvent.BACKEND_REALM_VLOBS_UPDATED,
                    {"realm_id": uid, "checkpoint": 2, "src_id": uid, "src_version": 2},
                ),
            ]
        )


@pytest.mark.trio
async def test_realm_notif_maintenance(running_backend, alice_backend_conn, alice2_user_fs):
    wid = await alice2_user_fs.workspace_create("foo")
    await alice2_user_fs.sync()

    with alice_backend_conn.event_bus.listen() as spy:
        # Start maintenance
        job = await alice2_user_fs.workspace_start_reencryption(wid)

        await spy.wait_multiple_with_timeout(
            [
                (
                    CoreEvent.BACKEND_REALM_MAINTENANCE_STARTED,
                    {"realm_id": wid, "encryption_revision": 2},
                ),
                # Receive the message containing the new key and encryption revision
                CoreEvent.BACKEND_MESSAGE_RECEIVED,
            ]
        )

    with alice_backend_conn.event_bus.listen() as spy:
        # Finish maintenance
        total, done = await job.do_one_batch()
        assert total == done

        await spy.wait_with_timeout(
            CoreEvent.BACKEND_REALM_MAINTENANCE_FINISHED,
            {"realm_id": wid, "encryption_revision": 2},
        )


@pytest.mark.trio
async def test_connection_refused(mock_clock, running_backend, event_bus, mallory):
    mock_clock.rate = 1.0
    conn = BackendAuthenticatedConn(
        mallory.organization_addr, mallory.device_id, mallory.signing_key, event_bus
    )
    with event_bus.listen() as spy:
        async with conn.run():

            # Wait for the connection to be initialized
            await spy.wait_with_timeout(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                {"status": BackendConnStatus.REFUSED, "status_exc": spy.ANY},
            )

            # Trying to use the connection should endup with an exception
            with pytest.raises(BackendConnectionRefused):
                await conn.cmds.ping()
