# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pytest
from pendulum import Pendulum

from parsec.api.data import UserManifest, WorkspaceEntry
from parsec.backend.realm import RealmGrantedRole, RealmRole
from parsec.core.core_events import CoreEvent
from parsec.core.fs import (
    FSBackendOfflineError,
    FSError,
    FSSharingNotAllowedError,
    FSWorkspaceNotFoundError,
)
from parsec.core.types import EntryID, LocalUserManifest, WorkspaceRole
from tests.common import create_shared_workspace, freeze_time


@pytest.mark.trio
async def test_share_unknown(running_backend, alice_user_fs, bob):
    wid = EntryID()
    with pytest.raises(FSWorkspaceNotFoundError):
        await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)


@pytest.mark.trio
async def test_share_to_oneself(running_backend, alice_user_fs, alice):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")

    with pytest.raises(FSError) as exc:
        await alice_user_fs.workspace_share(wid, alice.user_id, WorkspaceRole.MANAGER)
    assert str(exc.value) == "Cannot share to oneself"


@pytest.mark.trio
async def test_share_bad_recipient(running_backend, alice_user_fs, alice, mallory):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")

    with pytest.raises(FSError) as exc:
        await alice_user_fs.workspace_share(wid, mallory.user_id, WorkspaceRole.MANAGER)
    assert str(exc.value) == "Cannot retrieve recipient: User `mallory` doesn't exist in backend"


@pytest.mark.trio
async def test_share_offline(running_backend, alice_user_fs, bob):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")

    with running_backend.offline():
        with pytest.raises(FSBackendOfflineError):
            await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)


@pytest.mark.trio
@pytest.mark.parametrize("presynced", (True, False))
async def test_share_ok(running_backend, alice_user_fs, bob_user_fs, alice, bob, presynced):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")

    if presynced:
        await alice_user_fs.sync()

    await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)

    with bob_user_fs.event_bus.listen() as spy:
        with freeze_time("2000-01-03"):
            await bob_user_fs.process_last_messages()
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED,
        {
            "new_entry": WorkspaceEntry(
                name="w1",
                id=wid,
                key=ANY,
                encryption_revision=1,
                encrypted_on=Pendulum(2000, 1, 2),
                role_cached_on=Pendulum(2000, 1, 3),
                role=WorkspaceRole.MANAGER,
            ),
            "previous_entry": None,
        },
    )

    aum = alice_user_fs.get_user_manifest()
    bum = bob_user_fs.get_user_manifest()
    assert len(aum.workspaces) == 1
    assert len(bum.workspaces) == 1
    awe = aum.get_workspace_entry(wid)
    bwe = bum.get_workspace_entry(wid)

    assert bwe.name == "w1"
    assert bwe.id == awe.id
    assert bwe.role == WorkspaceRole.MANAGER

    aw = alice_user_fs.get_workspace(wid)
    bw = bob_user_fs.get_workspace(wid)
    aw_stat = await aw.path_info("/")
    bw_stat = await bw.path_info("/")
    assert aw_stat == bw_stat


@pytest.mark.trio
async def test_share_workspace_then_rename_it(
    running_backend, alice_user_fs, bob_user_fs, alice, bob
):
    # Share a workspace between Alice and Bob
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w")
    await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)
    with freeze_time("2000-01-03"):
        await bob_user_fs.process_last_messages()

    # Now Bob and alice both rename the workpsace for there own taste
    await bob_user_fs.workspace_rename(wid, "from_alice")
    await alice_user_fs.workspace_rename(wid, "to_bob")

    await bob_user_fs.sync()
    await alice_user_fs.sync()

    # This should have not changed the workspace in any way
    bw = bob_user_fs.get_workspace(wid)
    aw = alice_user_fs.get_workspace(wid)
    await bw.touch("/ping_bob.txt")
    await aw.mkdir("/ping_alice")

    await bw.sync()
    await aw.sync()
    await bw.sync()

    aw_stat = await aw.path_info("/")
    bw_stat = await bw.path_info("/")
    assert aw_stat == bw_stat
    assert aw_stat["id"] == wid


@pytest.mark.trio
async def test_unshare_ok(running_backend, alice_user_fs, bob_user_fs, alice, bob):
    # Share a workspace...
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
    await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.OWNER)
    await bob_user_fs.process_last_messages()

    # ...and unshare it
    await bob_user_fs.workspace_share(wid, alice.user_id, None)
    with alice_user_fs.event_bus.listen() as spy:
        with freeze_time("2000-01-03"):
            await alice_user_fs.process_last_messages()
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED,
        {
            "new_entry": WorkspaceEntry(
                name="w1",
                id=wid,
                key=ANY,
                encryption_revision=1,
                encrypted_on=Pendulum(2000, 1, 2),
                role_cached_on=Pendulum(2000, 1, 3),
                role=None,
            ),
            "previous_entry": WorkspaceEntry(
                name="w1",
                id=wid,
                key=ANY,
                encryption_revision=1,
                encrypted_on=Pendulum(2000, 1, 2),
                role_cached_on=Pendulum(2000, 1, 2),
                role=WorkspaceRole.OWNER,
            ),
        },
    )

    aum = alice_user_fs.get_user_manifest()
    aw = aum.workspaces[0]
    assert not aw.role

    # TODO: check workspace access is no longer possible


@pytest.mark.trio
async def test_unshare_not_shared(running_backend, alice_user_fs, bob_user_fs, alice, bob):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
    await alice_user_fs.workspace_share(wid, bob.user_id, None)
    with alice_user_fs.event_bus.listen() as spy:
        await bob_user_fs.process_last_messages()
    assert not spy.events

    # Workspace unsharing should have been ignored
    bum = bob_user_fs.get_user_manifest()
    assert not bum.workspaces


@pytest.mark.trio
async def test_share_to_another_after_beeing_unshared(
    running_backend, alice_user_fs, bob_user_fs, alice, bob
):
    # Share a workspace...
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
    await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)
    await bob_user_fs.process_last_messages()

    # ...and unshare it
    await alice_user_fs.workspace_share(wid, bob.user_id, None)
    await bob_user_fs.process_last_messages()

    # Shouldn't be able to share the workspace anymore
    with pytest.raises(FSSharingNotAllowedError):
        await bob_user_fs.workspace_share(wid, alice.user_id, None)


@pytest.mark.trio
async def test_reshare_workspace(running_backend, alice_user_fs, bob_user_fs, alice, bob):
    # Share a workspace...
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
    await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)
    with freeze_time("2000-01-03"):
        await bob_user_fs.process_last_messages()

    # ...and unshare it...
    await alice_user_fs.workspace_share(wid, bob.user_id, None)
    with freeze_time("2000-01-04"):
        await bob_user_fs.process_last_messages()

    # ...and re-share it !
    await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)
    with bob_user_fs.event_bus.listen() as spy:
        with freeze_time("2000-01-05"):
            await bob_user_fs.process_last_messages()
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED,
        {
            "new_entry": WorkspaceEntry(
                name="w1",
                id=wid,
                key=ANY,
                encryption_revision=1,
                encrypted_on=Pendulum(2000, 1, 2),
                role_cached_on=Pendulum(2000, 1, 5),
                role=WorkspaceRole.MANAGER,
            ),
            "previous_entry": WorkspaceEntry(
                name="w1",
                id=wid,
                key=ANY,
                encryption_revision=1,
                encrypted_on=Pendulum(2000, 1, 2),
                role_cached_on=Pendulum(2000, 1, 4),
                role=None,
            ),
        },
    )

    # Check access
    aum = alice_user_fs.get_user_manifest()
    bum = bob_user_fs.get_user_manifest()
    assert len(aum.workspaces) == 1
    assert len(bum.workspaces) == 1
    aw = aum.workspaces[0]
    bw = bum.workspaces[0]

    assert bw.name == "w1"
    assert bw.id == aw.id
    assert bw.role == WorkspaceRole.MANAGER


@pytest.mark.trio
async def test_share_with_different_role(running_backend, alice_user_fs, bob_user_fs, alice, bob):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
    aum = alice_user_fs.get_user_manifest()
    aw = aum.workspaces[0]

    previous_entry = None
    for role in WorkspaceRole:
        # (re)share with rights
        await alice_user_fs.workspace_share(wid, bob.user_id, role)
        with bob_user_fs.event_bus.listen() as spy:
            await bob_user_fs.process_last_messages()
        new_entry = spy.partial_obj(WorkspaceEntry, name="w1", id=wid, role=role)
        if not previous_entry:
            spy.assert_event_occured(
                CoreEvent.SHARING_UPDATED, {"new_entry": new_entry, "previous_entry": None}
            )

        else:
            spy.assert_event_occured(
                CoreEvent.SHARING_UPDATED,
                {"new_entry": new_entry, "previous_entry": previous_entry},
            )
        previous_entry = new_entry

        # Check access
        bum = bob_user_fs.get_user_manifest()
        assert len(bum.workspaces) == 1
        bw = bum.workspaces[0]

        assert bw.name == "w1"
        assert bw.id == aw.id
        assert bw.role == role


@pytest.mark.trio
async def test_share_no_manager_right(running_backend, alice_user_fs, alice, bob):
    with freeze_time("2000-01-02"):
        wid = await alice_user_fs.workspace_create("w1")
        await alice_user_fs.sync()

    # Drop manager right (and give to Bob the ownership)
    await running_backend.backend.realm.update_roles(
        alice.organization_id,
        RealmGrantedRole(
            realm_id=wid,
            user_id=bob.user_id,
            certificate=b"<dummy>",
            role=RealmRole.OWNER,
            granted_by=alice.device_id,
            granted_on=Pendulum(2000, 1, 3),
        ),
    )
    await running_backend.backend.realm.update_roles(
        alice.organization_id,
        RealmGrantedRole(
            realm_id=wid,
            user_id=alice.user_id,
            certificate=b"<dummy>",
            role=RealmRole.CONTRIBUTOR,
            granted_by=bob.device_id,
            granted_on=Pendulum(2000, 1, 4),
        ),
    )

    with pytest.raises(FSSharingNotAllowedError) as exc:
        await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)
    assert (
        exc.value.message
        == "Must be Owner or Manager on the workspace is mandatory to share it: {'status': 'not_allowed'}"
    )


@pytest.mark.trio
async def test_share_with_sharing_name_already_taken(
    running_backend, alice_user_fs, bob_user_fs, alice, bob
):
    # Bob and Alice both has a workspace with similar name
    with freeze_time("2000-01-01"):
        awid = await alice_user_fs.workspace_create("w")
        bwid = await bob_user_fs.workspace_create("w")
        bw2id = await bob_user_fs.workspace_create("w")

    # Sharing them shouldn't be a trouble
    await bob_user_fs.sync()
    await alice_user_fs.workspace_share(awid, bob.user_id, WorkspaceRole.MANAGER)

    # Bob should get a notification
    with bob_user_fs.event_bus.listen() as spy:
        with freeze_time("2000-01-02"):
            await bob_user_fs.process_last_messages()
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED,
        {
            "new_entry": WorkspaceEntry(
                name="w",
                id=awid,
                key=ANY,
                encryption_revision=1,
                encrypted_on=Pendulum(2000, 1, 1),
                role_cached_on=Pendulum(2000, 1, 2),
                role=WorkspaceRole.MANAGER,
            ),
            "previous_entry": None,
        },
    )

    assert len(bob_user_fs.get_user_manifest().workspaces) == 3

    b_aw_stat = await bob_user_fs.get_workspace(awid).path_info("/")
    a_aw_stat = await alice_user_fs.get_workspace(awid).path_info("/")
    b_aw_stat.pop("need_sync")
    a_aw_stat.pop("need_sync")
    assert b_aw_stat == a_aw_stat

    b_bw_stat = await bob_user_fs.get_workspace(bwid).path_info("/")
    assert b_bw_stat["id"] == bwid
    b_bw2_stat = await bob_user_fs.get_workspace(bw2id).path_info("/")
    assert b_bw2_stat["id"] == bw2id


@pytest.mark.trio
@pytest.mark.parametrize("first_to_sync", ("alice", "alice2"))
async def test_share_workspace_then_conflict_on_rights(
    running_backend, alice_user_fs, alice2_user_fs, bob_user_fs, alice, alice2, bob, first_to_sync
):
    # Bob shares a workspace with Alice...
    with freeze_time("2000-01-01"):
        wid = await bob_user_fs.workspace_create("w")
    with freeze_time("2000-01-02"):
        await bob_user_fs.workspace_share(wid, alice.user_id, WorkspaceRole.MANAGER)

    # ...but only Alice's first device get the information
    with freeze_time("2000-01-03"):
        await alice_user_fs.process_last_messages()

    # Now Bob change the sharing rights...
    with freeze_time("2000-01-04"):
        await bob_user_fs.workspace_share(wid, alice.user_id, WorkspaceRole.CONTRIBUTOR)

    # ...this time it's Alice's second device which get the info
    with freeze_time("2000-01-05"):
        # Note we will process the 2 sharing messages bob sent us, this
        # will attribute role_cached_on to the first message timestamp even
        # if we cache the second message role...
        await alice2_user_fs.process_last_messages()

    if first_to_sync == "alice":
        first = alice_user_fs
        second = alice2_user_fs
        synced_timestamp = Pendulum(2000, 1, 7)
        synced_version = 3
    else:
        first = alice2_user_fs
        second = alice_user_fs
        synced_timestamp = Pendulum(2000, 1, 6)
        synced_version = 2

    # Finally Alice devices try to reconciliate
    with freeze_time("2000-01-06"):
        await first.sync()
    with freeze_time("2000-01-07"):
        await second.sync()
    # Resync first device to get changes from the 2nd
    with freeze_time("2000-01-08"):
        await first.sync()

    am = alice_user_fs.get_user_manifest()
    a2m = alice2_user_fs.get_user_manifest()
    expected_remote = UserManifest(
        author=alice2.device_id,
        timestamp=synced_timestamp,
        id=alice2.user_manifest_id,
        version=synced_version,
        created=Pendulum(2000, 1, 1),
        updated=Pendulum(2000, 1, 5),
        last_processed_message=2,
        workspaces=(
            WorkspaceEntry(
                name="w",
                id=wid,
                key=ANY,
                encryption_revision=1,
                encrypted_on=Pendulum(2000, 1, 1),
                role_cached_on=Pendulum(2000, 1, 5),
                role=WorkspaceRole.CONTRIBUTOR,
            ),
        ),
    )
    expected = LocalUserManifest(
        base=expected_remote,
        need_sync=False,
        updated=expected_remote.updated,
        last_processed_message=expected_remote.last_processed_message,
        workspaces=expected_remote.workspaces,
    )
    assert am == expected
    assert a2m == expected

    a_w = alice_user_fs.get_workspace(wid)
    a2_w = alice2_user_fs.get_workspace(wid)

    a_w_stat = await a_w.path_info("/")
    a2_w_stat = await a2_w.path_info("/")

    a_w_entry = a_w.get_workspace_entry()
    a2_w_entry = a2_w.get_workspace_entry()

    assert a_w_stat == {
        "type": "folder",
        "is_placeholder": False,
        "id": wid,
        "created": ANY,
        "updated": ANY,
        "base_version": 1,
        "need_sync": False,
        "children": [],
    }
    assert a_w_stat == a2_w_stat

    assert a_w_entry == WorkspaceEntry(
        name=f"w",
        id=wid,
        key=ANY,
        encryption_revision=1,
        encrypted_on=Pendulum(2000, 1, 1),
        role_cached_on=Pendulum(2000, 1, 5),
        role=WorkspaceRole.CONTRIBUTOR,
    )
    assert a2_w_entry == a_w_entry


@pytest.mark.trio
async def test_sharing_events_triggered_on_sync(
    running_backend, alice_user_fs, alice2_user_fs, bob_user_fs, alice, bob
):
    # Share a first workspace
    with freeze_time("2000-01-02"):
        wid = await create_shared_workspace("w", bob_user_fs, alice_user_fs)

    with alice2_user_fs.event_bus.listen() as spy:
        await alice2_user_fs.sync()
    expected_entry_v1 = WorkspaceEntry(
        name="w",
        id=wid,
        key=ANY,
        encryption_revision=1,
        encrypted_on=Pendulum(2000, 1, 2),
        role_cached_on=Pendulum(2000, 1, 2),
        role=WorkspaceRole.MANAGER,
    )
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED, {"new_entry": expected_entry_v1, "previous_entry": None}
    )

    # Change role
    await bob_user_fs.workspace_share(wid, alice.user_id, WorkspaceRole.OWNER)
    with freeze_time("2000-01-03"):
        await alice_user_fs.process_last_messages()
    await alice_user_fs.sync()

    with alice2_user_fs.event_bus.listen() as spy:
        await alice2_user_fs.sync()
    expected_entry_v2 = WorkspaceEntry(
        name="w",
        id=wid,
        key=ANY,
        encryption_revision=1,
        encrypted_on=Pendulum(2000, 1, 2),
        role_cached_on=Pendulum(2000, 1, 3),
        role=WorkspaceRole.OWNER,
    )
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED,
        {"new_entry": expected_entry_v2, "previous_entry": expected_entry_v1},
    )

    # Revoke
    await bob_user_fs.workspace_share(wid, alice.user_id, None)
    with freeze_time("2000-01-04"):
        await alice_user_fs.process_last_messages()
    await alice_user_fs.sync()

    with alice2_user_fs.event_bus.listen() as spy:
        await alice2_user_fs.sync()
    expected_entry_v3 = WorkspaceEntry(
        name="w",
        id=wid,
        key=ANY,
        encryption_revision=1,
        encrypted_on=Pendulum(2000, 1, 2),
        role_cached_on=Pendulum(2000, 1, 4),
        role=None,
    )
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED,
        {"new_entry": expected_entry_v3, "previous_entry": expected_entry_v2},
    )


@pytest.mark.trio
async def test_no_sharing_event_on_sync_on_unknown_workspace(
    running_backend, alice_user_fs, alice2_user_fs, bob_user_fs, alice, bob
):
    # Share a workspace...
    wid = await create_shared_workspace("w", bob_user_fs, alice_user_fs)

    # ...and unshare it before alice2 even know about it
    await bob_user_fs.workspace_share(wid, alice.user_id, None)
    await alice_user_fs.process_last_messages()
    await alice_user_fs.sync()

    # No sharing event should be triggered !
    with alice2_user_fs.event_bus.listen() as spy:
        await alice2_user_fs.sync()
    spy.assert_events_exactly_occured([CoreEvent.FS_ENTRY_REMOTE_CHANGED])


@pytest.mark.trio
async def test_sharing_event_on_sync_if_same_role(
    running_backend, alice_user_fs, alice2_user_fs, bob_user_fs, alice, bob
):
    # Share a workspace, alice2 knows about it
    with freeze_time("2000-01-02"):
        wid = await create_shared_workspace("w", bob_user_fs, alice_user_fs, alice2_user_fs)
    expected_entry_v1 = WorkspaceEntry(
        name="w",
        id=wid,
        key=ANY,
        encryption_revision=1,
        encrypted_on=Pendulum(2000, 1, 2),
        role_cached_on=Pendulum(2000, 1, 2),
        role=WorkspaceRole.MANAGER,
    )

    # Then change alice's role...
    await bob_user_fs.workspace_share(wid, alice.user_id, WorkspaceRole.OWNER)
    with freeze_time("2000-01-03"):
        await alice_user_fs.process_last_messages()
    await alice_user_fs.sync()

    # ...and give back alice the same role
    await bob_user_fs.workspace_share(wid, alice.user_id, WorkspaceRole.MANAGER)
    with freeze_time("2000-01-04"):
        await alice_user_fs.process_last_messages()
    expected_entry_v3 = expected_entry_v1.evolve(role_cached_on=Pendulum(2000, 1, 4))
    await alice_user_fs.sync()

    # A single sharing event should be triggered
    with alice2_user_fs.event_bus.listen() as spy:
        await alice2_user_fs.sync()
    spy.assert_event_occured(
        CoreEvent.SHARING_UPDATED,
        {"new_entry": expected_entry_v3, "previous_entry": expected_entry_v1},
    )
