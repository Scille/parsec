# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from async_generator import asynccontextmanager

from parsec.core.core_events import CoreEvent
from tests.common import freeze_time
from tests.open_tcp_stream_mock_wrapper import offline


@asynccontextmanager
async def wait_for_entries_synced(core, entries_pathes):
    event = trio.Event()
    to_sync = set(entries_pathes)
    synced = set()

    def _on_entry_synced(sender, id, path):
        if event.is_set():
            return

        if path not in to_sync:
            raise AssertionError(f"{path} wasn't supposed to be synced, expected only {to_sync}")
        if path in synced:
            raise AssertionError(
                f"{path} synced two time while waiting synchro for {to_sync - synced}"
            )

        synced.add(path)
        if synced == to_sync:
            event.set()

    core.signal_ns.connect(CoreEvent.FS_ENTRY_SYNCED, _on_entry_synced)
    try:
        yield event
        await event.wait()

    finally:
        core.signal_ns.disconnect(CoreEvent.FS_ENTRY_SYNCED, _on_entry_synced)


# @pytest.mark.trio
# async def test_online_sync(mock_clock, running_backend, alice_core, alice2_core2):
#     await alice_core.event_bus.spy.wait_for_backend_connection_ready()
#     await alice2_core2.event_bus.spy.wait_for_backend_connection_ready()

#     with alice_core.event_bus.listen() as events, alice2_core2.event_bus.listen() as events2:
#         await events.wait("fs.sync_monitor.idle")
#         await events2.wait("fs.sync_monitor.idle")

#         with freeze_time("2000-01-02"):
#             await alice_core.user_fs.file_create("/foo.txt")

#         with freeze_time("2000-01-03"):
#             await alice_core.user_fs.file_write("/foo.txt", b"hello world !")

#         mock_clock.autojump_threshold = 0.1
#         await events.wait("fs.sync_monitor.tick_finished")
#         await events2.wait("fs.sync_monitor.tick_finished")

#     stat = await alice_core.user_fs.stat("/foo.txt")
#     stat2 = await alice2_core2.user_fs.stat("/foo.txt")
#     assert stat2 == stat


@pytest.mark.trio
@pytest.mark.skip(reason="Recursive sync strategy need to be reworked")
async def test_online_sync2(mock_clock, running_backend, core_factory, alice, alice2):
    mock_clock.autojump_threshold = 0.1

    # Given the cores are initialized while the backend is online, we are
    # guaranteed they are connected
    async with core_factory() as alice_core, core_factory() as alice2_core2:
        await alice_core.login(alice)
        await alice2_core2.login(alice2)

        # FS does a full sync at startup, wait for it to finish
        await alice_core.user_fs.wait_not_syncing()
        await alice2_core2.user_fs.wait_not_syncing()

        async with wait_for_entries_synced(alice2_core2, ["/"]), wait_for_entries_synced(
            alice_core, ("/", "/foo.txt")
        ):

            with freeze_time("2000-01-02"):
                await alice_core.user_fs.touch("/foo.txt")

            with freeze_time("2000-01-03"):
                await alice_core.user_fs.file_write("/foo.txt", b"hello world !")

            await alice_core.user_fs.sync("/foo.txt")

        stat = await alice_core.user_fs.stat("/foo.txt")
        stat2 = await alice2_core2.user_fs.stat("/foo.txt")
        assert stat2 == stat


@pytest.mark.trio
@pytest.mark.skip(reason="Recursive sync strategy need to be reworked")
async def test_sync_then_clean_start(mock_clock, running_backend, core_factory, alice, alice2):
    mock_clock.autojump_threshold = 0.1

    # Given the cores are initialized while the backend is online, we are
    # guaranteed they are connected
    async with core_factory() as alice_core:
        await alice_core.login(alice)

        async with wait_for_entries_synced(alice_core, ("/", "/foo.txt")):

            with freeze_time("2000-01-02"):
                await alice_core.user_fs.touch("/foo.txt")

            with freeze_time("2000-01-03"):
                await alice_core.user_fs.file_write("/foo.txt", b"v1")

            await alice_core.user_fs.sync("/foo.txt")

        async with core_factory() as alice2_core2, wait_for_entries_synced(alice2_core2, ["/"]):
            await alice2_core2.login(alice2)

            for path in ("/", "/foo.txt"):
                stat = await alice_core.user_fs.stat(path)
                stat2 = await alice2_core2.user_fs.stat(path)
                assert stat2 == stat


@pytest.mark.trio
@pytest.mark.skip(reason="Recursive sync strategy need to be reworked")
async def test_sync_then_fast_forward_on_start(
    mock_clock, running_backend, core_factory, alice, alice2
):
    mock_clock.autojump_threshold = 0.1

    # Given the cores are initialized while the backend is online, we are
    # guaranteed they are connected
    async with core_factory() as alice_core, core_factory() as alice2_core2:
        await alice_core.login(alice)
        await alice2_core2.login(alice2)

        with freeze_time("2000-01-02"):
            await alice_core.user_fs.touch("/foo.txt")

        with freeze_time("2000-01-03"):
            await alice_core.user_fs.file_write("/foo.txt", b"v1")

        async with wait_for_entries_synced(alice2_core2, ["/"]), wait_for_entries_synced(
            alice_core, ("/", "/foo.txt")
        ):
            await alice_core.user_fs.sync("/foo.txt")

        await alice2_core2.logout()

        with freeze_time("2000-01-04"):
            await alice_core.user_fs.file_write("/foo.txt", b"v2")
            await alice_core.user_fs.folder_create("/bar")

        async with wait_for_entries_synced(alice_core, ["/", "/bar", "/foo.txt"]):
            await alice_core.user_fs.sync()

        async with wait_for_entries_synced(alice2_core2, ["/"]):
            await alice2_core2.login(alice2)

        for path in ("/", "/bar", "/foo.txt"):
            stat = await alice_core.user_fs.stat(path)
            stat2 = await alice2_core2.user_fs.stat(path)
            assert stat2 == stat


@pytest.mark.trio
@pytest.mark.skip(reason="Recursive sync strategy need to be reworked")
async def test_fast_forward_on_offline_during_sync(
    mock_clock, server_factory, backend, core_factory, alice, alice2
):
    mock_clock.rate = 10

    # Create two servers to be able to turn offline a single one
    async with server_factory(backend.handle_client) as server1, server_factory(
        backend.handle_client
    ) as server2:

        # Given the cores are initialized while the backend is online, we are
        # guaranteed they are connected
        async with core_factory(config={"backend_addr": server1.addr}) as alice_core, core_factory(
            config={"backend_addr": server2.addr}
        ) as alice2_core2:
            await alice_core.login(alice)
            await alice2_core2.login(alice2)

            # TODO: shouldn't need this...
            await trio.testing.wait_all_tasks_blocked(cushion=0.1)

            async with wait_for_entries_synced(alice2_core2, ["/"]), wait_for_entries_synced(
                alice_core, ("/", "/foo.txt")
            ):
                with freeze_time("2000-01-02"):
                    await alice_core.user_fs.touch("/foo.txt")

                with freeze_time("2000-01-03"):
                    await alice_core.user_fs.file_write("/foo.txt", b"v1")

                # Sync should be done in the background by the sync monitor
                ########### shouldn't need to do that... #######
                await alice_core.user_fs.sync("/foo.txt")

            # TODO: shouldn't need this...
            await trio.testing.wait_all_tasks_blocked(cushion=0.1)

            # core goes offline, other core2 is still connected to backend
            async with wait_for_entries_synced(alice_core, ("/", "/foo.txt")):
                stat2 = await alice2_core2.user_fs.stat("/foo.txt")
                with offline(server1.addr):

                    with freeze_time("2000-01-04"):
                        await alice2_core2.user_fs.file_write("/foo.txt", b"v2")
                        await alice2_core2.user_fs.folder_create("/bar")

                    async with wait_for_entries_synced(alice2_core2, ("/", "/bar", "/foo.txt")):
                        await alice2_core2.user_fs.sync()

            for path in ("/", "/bar", "/foo.txt"):
                stat = await alice_core.user_fs.stat(path)
                stat2 = await alice2_core2.user_fs.stat(path)
                assert stat2 == stat
