# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum
from unittest.mock import ANY

from parsec.core.types import EntryID
from parsec.core.fs import (
    FSError,
    FSWorkspaceNotFoundError,
    FSWorkspaceNotInMaintenance,
    FSWorkspaceInMaintenance,
    FSBadEncryptionRevision,
)

from tests.common import freeze_time


@pytest.fixture
async def workspace(running_backend, alice_user_fs):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
        # Sync workspace manifest v1
        await alice_user_fs.sync()
        w = alice_user_fs.get_workspace(wid)
        await w.touch("/foo.txt")
        # Sync workspace manifest v1 + file manifest v1
        await w.sync()
        await w.write_bytes("/foo.txt", b"v2")
        # Sync file manifest v2
        await w.sync()

        # Should end up with 4 vlob atoms in the workspace

    return wid


@pytest.mark.trio
async def test_do_reencryption(running_backend, workspace, alice, alice_user_fs):
    with running_backend.backend.event_bus.listen() as spy:
        job = await alice_user_fs.workspace_start_reencryption(workspace)

        # Check events
        await spy.wait_multiple_with_timeout(
            [
                (
                    "realm.maintenance_started",
                    {
                        "organization_id": alice.organization_id,
                        "author": alice.device_id,
                        "realm_id": workspace,
                        "encryption_revision": 2,
                        "garbage_collection_revision": 0,
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

    total, done = await job.do_one_batch(size=1)
    assert total == 4
    assert done == 1

    total, done = await job.do_one_batch(size=2)
    assert total == 4
    assert done == 3

    total, done = await job.do_one_batch(size=2)
    assert total == 4
    assert done == 4

    with pytest.raises(FSWorkspaceNotInMaintenance):
        await job.do_one_batch()


@pytest.mark.trio
async def test_reencrypt_placeholder(running_backend, alice, alice_user_fs):
    wid = await alice_user_fs.workspace_create("w1")
    with pytest.raises(FSError):
        await alice_user_fs.workspace_start_reencryption(wid)


@pytest.mark.trio
async def test_unknown_workspace(alice_user_fs):
    bad_wid = EntryID()

    with pytest.raises(FSWorkspaceNotFoundError):
        await alice_user_fs.workspace_start_reencryption(bad_wid)

    with pytest.raises(FSWorkspaceNotFoundError):
        await alice_user_fs.workspace_continue_reencryption(bad_wid)


@pytest.mark.trio
async def test_concurrent_start_reencryption(workspace, alice_user_fs):
    await alice_user_fs.workspace_start_reencryption(workspace)

    with pytest.raises(FSWorkspaceInMaintenance):
        await alice_user_fs.workspace_start_reencryption(workspace)


@pytest.mark.trio
async def test_continue_reencryption_not_in_maintenance(workspace, alice_user_fs):
    with pytest.raises(FSWorkspaceNotInMaintenance):
        await alice_user_fs.workspace_continue_reencryption(workspace)


@pytest.mark.trio
async def test_continue_reencryption_with_bad_encryption_revision(workspace, alice_user_fs):
    await alice_user_fs.workspace_start_reencryption(workspace)

    with pytest.raises(FSError):
        await alice_user_fs.workspace_continue_reencryption(workspace)


@pytest.mark.trio
async def test_concurrent_continue_reencryption(running_backend, workspace, alice_user_fs):
    with running_backend.backend.event_bus.listen() as spy:
        job1 = await alice_user_fs.workspace_start_reencryption(workspace)

        await spy.wait_with_timeout("message.received")

    # Update encryption_revision in user manifest
    await alice_user_fs.process_last_messages()

    job2 = await alice_user_fs.workspace_continue_reencryption(workspace)

    total, done = await job1.do_one_batch(size=1)
    assert total == 4
    assert done == 1

    total, done = await job2.do_one_batch(size=2)
    assert total == 4
    assert done == 3

    total, done = await job1.do_one_batch(size=2)
    assert total == 4
    assert done == 4

    with pytest.raises(FSWorkspaceNotInMaintenance):
        await job2.do_one_batch()

    with pytest.raises(FSWorkspaceNotInMaintenance):
        await job1.do_one_batch()


@pytest.mark.trio
async def test_reencryption_already_started(running_backend, alice_user_fs):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
    await alice_user_fs.sync()

    await alice_user_fs.workspace_start_reencryption(wid)

    with pytest.raises(FSError):
        await alice_user_fs.workspace_start_reencryption(wid)


@pytest.mark.trio
async def test_no_access_during_reencryption(running_backend, alice2_user_fs, workspace):
    # Workspace have been created with alice_user_fs, hence user alice2_user_fs
    # start with no local cache
    await alice2_user_fs.sync()
    aw = alice2_user_fs.get_workspace(workspace)

    # Populate local cache for workspace root manifest
    await aw.path_info("/")

    # Start reencryption
    job = await alice2_user_fs.workspace_start_reencryption(workspace)

    # WorkspaceFS doesn't have encryption revision until user messages are processed
    assert aw.get_encryption_revision() == 1
    # Data not in local cache cannot be accessed
    root_info = await aw.path_info("/")
    assert root_info == {
        "id": workspace,
        "type": "folder",
        "base_version": 2,
        "created": Pendulum(2000, 1, 2),
        "updated": Pendulum(2000, 1, 2),
        "is_placeholder": False,
        "need_sync": False,
        "children": ["foo.txt"],
    }
    with pytest.raises(FSWorkspaceInMaintenance):
        await aw.path_info("/foo.txt")

    # Also cannot sync data
    with freeze_time("2000-01-03"):
        await aw.touch("/bar.txt")
        bar_id = await aw.path_id("/bar.txt")
    with pytest.raises(FSWorkspaceInMaintenance):
        await aw.sync_by_id(bar_id)

    # Finish reencryption
    while True:
        total, done = await job.do_one_batch()
        if total == done:
            break

    # Still not allowed to access data due to outdated encryption_revision
    root_info2 = await aw.path_info("/")
    assert root_info2 == {
        "id": workspace,
        "type": "folder",
        "base_version": 2,
        "created": Pendulum(2000, 1, 2),
        "updated": Pendulum(2000, 1, 3),
        "is_placeholder": False,
        "need_sync": True,
        "children": ["bar.txt", "foo.txt"],
    }
    with pytest.raises(FSBadEncryptionRevision):
        await aw.path_info("/foo.txt")

    # Stilly not allowed to do the sync
    with pytest.raises(FSBadEncryptionRevision):
        await aw.sync_by_id(bar_id)

    # Update encryption_revision in user manifest and check access is ok
    await alice2_user_fs.process_last_messages()
    assert aw.get_encryption_revision() == 2
    file_info = await aw.path_info("/foo.txt")
    assert file_info == {
        "id": ANY,
        "type": "file",
        "base_version": 2,
        "created": Pendulum(2000, 1, 2),
        "updated": Pendulum(2000, 1, 2),
        "is_placeholder": False,
        "need_sync": False,
        "size": 2,
    }

    # Finally sync is ok
    await aw.sync_by_id(bar_id)
