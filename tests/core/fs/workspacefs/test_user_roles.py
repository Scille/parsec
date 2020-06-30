# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.core.fs import FSBackendOfflineError
from parsec.core.types import WorkspaceRole


@pytest.mark.trio
async def test_on_shared(running_backend, alice_user_fs, alice, bob):
    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)
    roles = await workspace.get_user_roles()
    assert roles == {alice.user_id: WorkspaceRole.OWNER, bob.user_id: WorkspaceRole.MANAGER}


@pytest.mark.trio
async def test_manifest_not_in_local_cache(running_backend, alice_user_fs, alice2_user_fs, alice):
    wid = await alice_user_fs.workspace_create("w")
    await alice_user_fs.sync()
    await alice2_user_fs.sync()
    workspace = alice2_user_fs.get_workspace(wid)
    roles = await workspace.get_user_roles()
    assert roles == {alice.user_id: WorkspaceRole.OWNER}


@pytest.mark.trio
async def test_on_placeholder(alice_user_fs, alice):
    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    roles = await workspace.get_user_roles()
    assert roles == {alice.user_id: WorkspaceRole.OWNER}


@pytest.mark.trio
async def test_while_offline_on_non_placeholder(running_backend, alice_user_fs):
    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    await alice_user_fs.sync()
    with running_backend.offline():
        with pytest.raises(FSBackendOfflineError):
            await workspace.get_user_roles()
