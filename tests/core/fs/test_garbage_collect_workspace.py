# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS
import pytest
from pendulum import now as pendulum_now

from parsec.core.types import EntryID
from parsec.core.fs import FSError, FSWorkspaceNotInMaintenance, FSWorkspaceNotFoundError

from tests.common import freeze_time


@pytest.fixture
async def workspace(running_backend, alice_user_fs):

    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
        # Sync workspace manifest v1
        await alice_user_fs.sync()
    return wid


@pytest.fixture
async def vlob(running_backend, alice_user_fs, workspace):
    workspace = alice_user_fs.get_workspace(workspace)
    now = pendulum_now()
    with freeze_time(now.add(months=-18).to_date_string()):
        await workspace.touch("/foo.txt")
        await workspace.sync()

        await workspace.write_bytes("/foo.txt", b"0")
        await workspace.sync()
    with freeze_time(now.add(months=-15).to_date_string()):
        await workspace.write_bytes("/foo.txt", b"1")
        await workspace.sync()

    with freeze_time(now.add(months=-14).to_date_string()):

        await workspace.write_bytes("/foo.txt", b"2")
        await workspace.sync()
        num_chars = 512 * 1024
        await workspace.write_bytes("/foo.txt", b"v3" * num_chars)
        await workspace.sync()
    with freeze_time(now.add(months=-13).to_date_string()):
        await workspace.write_bytes("/foo.txt", b"4")
        await workspace.sync()

    with freeze_time(now.add(months=-10).to_date_string()):
        await workspace.write_bytes("/foo.txt", b"5")
        await workspace.sync()

    six_month_ago = now.add(months=-5)
    with freeze_time(six_month_ago.add(weeks=1).to_date_string()):
        await workspace.write_bytes("/foo.txt", b"8")
        await workspace.sync()
        await workspace.write_bytes("/foo.txt", b"9")
        await workspace.sync()
    with freeze_time(six_month_ago.add(weeks=2).to_date_string()):
        await workspace.write_bytes("/foo.txt", b"10")
        await workspace.sync()
        await workspace.write_bytes("/foo.txt", b"11")
        await workspace.sync()

    return await workspace.path_id("/foo.txt")


@pytest.mark.trio
async def test_do_garbage_collection(running_backend, workspace, alice, alice_user_fs, vlob):

    with running_backend.backend.event_bus.listen() as spy:
        job = await alice_user_fs.workspace_start_garbage_collection(workspace)
        # Check events
        await spy.wait_multiple_with_timeout(
            [
                (
                    "realm.maintenance_started",
                    {
                        "organization_id": alice.organization_id,
                        "author": alice.device_id,
                        "realm_id": workspace,
                        "encryption_revision": 1,
                        "garbage_collection_revision": 1,
                    },
                ),
                (
                    "message.received",
                    {
                        "organization_id": alice.organization_id,
                        "author": alice.device_id,
                        "recipient": alice.user_id,
                        "index": 1,
                    },
                ),
            ]
        )
        now = pendulum_now()
        with freeze_time(now.to_date_string()):

            total, done = await job.do_one_batch(size=1)
            assert total == 13
            assert done == 1

            total, done = await job.do_one_batch(size=3)
            assert total == 13
            assert done == 4

            total, done = await job.do_one_batch(size=5)
            assert total == 13
            assert done == 9

            total, done = await job.do_one_batch(size=4)
            assert total == 13
            assert done == 13

            with pytest.raises(FSWorkspaceNotInMaintenance):
                await job.do_one_batch()


@pytest.mark.trio
async def test_garbage_collect_placeholder(running_backend, alice, alice_user_fs):
    wid = await alice_user_fs.workspace_create("w1")
    with pytest.raises(FSError):
        await alice_user_fs.workspace_start_garbage_collection(wid)


@pytest.mark.trio
async def test_unknown_workspace(alice_user_fs):
    bad_wid = EntryID()

    with pytest.raises(FSWorkspaceNotFoundError):
        await alice_user_fs.workspace_start_garbage_collection(bad_wid)

    with pytest.raises(FSWorkspaceNotFoundError):
        await alice_user_fs.workspace_start_garbage_collection(bad_wid)


@pytest.mark.trio
async def test_garbage_collection_already_started(running_backend, alice_user_fs):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
    await alice_user_fs.sync()

    await alice_user_fs.workspace_start_garbage_collection(wid)

    with pytest.raises(FSError):
        await alice_user_fs.workspace_start_garbage_collection(wid)
