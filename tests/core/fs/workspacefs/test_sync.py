# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from functools import partial

import pytest

from parsec.core.types import FsPath
from tests.common import create_shared_workspace


@pytest.fixture
@pytest.mark.trio
async def shared_workspaces(alice_user_fs, bob_user_fs, running_backend):
    wid = await create_shared_workspace("w", alice_user_fs, bob_user_fs)
    alice_workspace = alice_user_fs.get_workspace(wid)
    bob_workspace = bob_user_fs.get_workspace(wid)
    return alice_workspace, bob_workspace


@pytest.fixture
def alice_workspace(shared_workspaces):
    alice_workspace, bob_workspace = shared_workspaces
    return alice_workspace


@pytest.fixture
def bob_workspace(shared_workspaces):
    alice_workspace, bob_workspace = shared_workspaces
    return bob_workspace


@pytest.mark.trio
@pytest.mark.parametrize("remote_changed", [False, True])
async def test_sync_by_id_single(alice_workspace, remote_changed):
    # Assuming the the remote has changed when it hasn't should not be an issue
    sync_by_id = partial(alice_workspace.sync_by_id, remote_changed=remote_changed)
    entry = alice_workspace.get_workspace_entry()
    wid = entry.id

    # Empty workspace
    await sync_by_id(wid)

    # Empty directory
    await alice_workspace.mkdir("/a")
    a_id = await alice_workspace.path_id("/a")
    await sync_by_id(a_id)

    # Non empty workspace
    await alice_workspace.sync_by_id(wid)

    # Directory with folder placeholders
    await alice_workspace.mkdir("/i/j/k", parents=True)
    i_id = await alice_workspace.path_id("/i")
    await sync_by_id(i_id)

    # Workspace with folder placeholders
    await alice_workspace.mkdir("/x/y/z", parents=True)
    await sync_by_id(wid)

    # Workspace with file placeholders
    await alice_workspace.touch("/f")
    await alice_workspace.write_bytes("/f", b"abc")
    await sync_by_id(wid)

    # Non empty file
    await alice_workspace.write_bytes("/f", b"efg", offset=3)
    f_id = await alice_workspace.path_id("/f")
    await sync_by_id(f_id)
    assert await alice_workspace.read_bytes("/f") == b"abcefg"


@pytest.mark.trio
async def test_sync_by_id_couple(alice_workspace, bob_workspace):
    alice_entry = alice_workspace.get_workspace_entry()
    alice_wid = alice_entry.id
    bob_entry = bob_workspace.get_workspace_entry()
    bob_wid = bob_entry.id

    # Create directories
    await alice_workspace.mkdir("/a")
    await bob_workspace.mkdir("/b")

    # Alice sync /a
    await alice_workspace.sync_by_id(alice_wid)

    # Bob sync /b and doesn't know about alice /a yet
    await bob_workspace.sync_by_id(bob_wid, remote_changed=False)

    # Alice knows about bob /b
    await alice_workspace.sync_by_id(alice_wid)

    # Everyone should be up to date by now
    expected = [FsPath("/a"), FsPath("/b")]
    assert await bob_workspace.listdir("/") == expected
    assert await alice_workspace.listdir("/") == expected
