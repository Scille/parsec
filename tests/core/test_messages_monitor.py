# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio
import pytest
from unittest.mock import ANY

from tests.common import create_shared_workspace, freeze_time

from parsec.core.types import WorkspaceEntry, WorkspaceRole


@pytest.mark.trio
async def test_new_sharing_trigger_event(alice_core, bob_core, running_backend):
    await alice_core.event_bus.spy.wait_for_backend_connection_ready()
    await bob_core.event_bus.spy.wait_for_backend_connection_ready()

    # First, create a folder and sync it on backend
    wid = await alice_core.user_fs.workspace_create("foo")
    workspace = alice_core.user_fs.get_workspace(wid)
    await workspace.sync("/")

    # Now we can share this workspace with Bob
    with bob_core.event_bus.listen() as spy:
        with freeze_time("2000-01-02"):
            await alice_core.user_fs.workspace_share(
                wid, recipient="bob", role=WorkspaceRole.MANAGER
            )

        # Bob should get a notification
        with trio.fail_after(seconds=1):
            await spy.wait(
                "sharing.granted",
                kwargs={
                    "new_entry": WorkspaceEntry(
                        name="foo (shared by alice)",
                        id=wid,
                        key=ANY,
                        encryption_revision=1,
                        role_cached_on=ANY,
                        role=WorkspaceRole.MANAGER,
                    )
                },
            )


@pytest.mark.trio
async def test_revoke_sharing_trigger_event(alice_core, bob_core, running_backend):
    with freeze_time("2000-01-02"):
        wid = await create_shared_workspace("w", alice_core, bob_core)

    with bob_core.event_bus.listen() as spy:
        with freeze_time("2000-01-03"):
            await alice_core.user_fs.workspace_share(wid, recipient="bob", role=None)

        # Each workspace participant should get the message
        with trio.fail_after(seconds=1):
            await spy.wait(
                "sharing.revoked",
                kwargs={
                    "new_entry": WorkspaceEntry(
                        name="w (shared by alice)",
                        id=wid,
                        key=ANY,
                        encryption_revision=1,
                        role_cached_on=ANY,
                        role=None,
                    ),
                    "previous_entry": WorkspaceEntry(
                        name="w (shared by alice)",
                        id=wid,
                        key=ANY,
                        encryption_revision=1,
                        role_cached_on=ANY,
                        role=WorkspaceRole.MANAGER,
                    ),
                },
            )


@pytest.mark.trio
async def test_new_reencryption_trigger_event(alice_core, bob_core, running_backend):
    with freeze_time("2000-01-02"):
        wid = await create_shared_workspace("w", alice_core, bob_core)

    with alice_core.event_bus.listen() as aspy, bob_core.event_bus.listen() as bspy:
        with freeze_time("2000-01-03"):
            await alice_core.user_fs.workspace_start_reencryption(wid)

        # Each workspace participant should get the message
        with trio.fail_after(seconds=1):
            await aspy.wait(
                "sharing.updated",
                kwargs={
                    "new_entry": WorkspaceEntry(
                        name="w",
                        id=wid,
                        key=ANY,
                        encryption_revision=2,
                        role_cached_on=ANY,
                        role=WorkspaceRole.OWNER,
                    ),
                    "previous_entry": WorkspaceEntry(
                        name="w",
                        id=wid,
                        key=ANY,
                        encryption_revision=1,
                        role_cached_on=ANY,
                        role=WorkspaceRole.OWNER,
                    ),
                },
            )
            await bspy.wait(
                "sharing.updated",
                kwargs={
                    "new_entry": WorkspaceEntry(
                        name="w (shared by alice)",
                        id=wid,
                        key=ANY,
                        encryption_revision=2,
                        role_cached_on=ANY,
                        role=WorkspaceRole.MANAGER,
                    ),
                    "previous_entry": WorkspaceEntry(
                        name="w (shared by alice)",
                        id=wid,
                        key=ANY,
                        encryption_revision=1,
                        role_cached_on=ANY,
                        role=WorkspaceRole.MANAGER,
                    ),
                },
            )
