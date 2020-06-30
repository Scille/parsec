# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.api.protocol import DeviceID
from parsec.core.core_events import CoreEvent
from parsec.core.fs.exceptions import FSFileConflictError
from parsec.core.fs.workspacefs.sync_transactions import merge_folder_children, merge_manifests
from parsec.core.types import Chunk, EntryID, FsPath, LocalFileManifest, LocalFolderManifest


def test_merge_folder_children():
    m1 = EntryID()
    m2 = EntryID()
    a1 = {"a": m1}
    a2 = {"a": m2}
    b1 = {"b.txt": m1}
    b2 = {"b.txt": m2}
    c1 = {"c.tar.gz": m1}
    c2 = {"c.tar.gz": m2}

    # Empty folder
    assert merge_folder_children({}, {}, {}, "a@a") == {}

    # Adding children
    assert merge_folder_children({}, a1, {}, "a@a") == a1
    assert merge_folder_children({}, {}, a1, "a@a") == a1
    assert merge_folder_children({}, a1, a1, "a@a") == a1

    # Removing children
    assert merge_folder_children(a1, {}, a1, "a@a") == {}
    assert merge_folder_children(a1, a1, {}, "a@a") == {}
    assert merge_folder_children(a1, {}, {}, "a@a") == {}

    # Renaming children
    assert merge_folder_children(a1, a1, b1, "a@a") == b1
    assert merge_folder_children(a1, b1, a1, "a@a") == b1
    assert merge_folder_children(a1, b1, b1, "a@a") == b1

    # Conflicting renaming
    result = merge_folder_children(a1, b1, c1, "a@a")
    assert result == {"c (renamed by a@a).tar.gz": m1}

    # Conflicting names
    result = merge_folder_children({}, a1, a2, "a@a")
    assert result == {"a": m2, "a (conflicting with a@a)": m1}
    result = merge_folder_children({}, b1, b2, "a@a")
    assert result == {"b.txt": m2, "b (conflicting with a@a).txt": m1}
    result = merge_folder_children({}, c1, c2, "a@a")
    assert result == {"c.tar.gz": m2, "c (conflicting with a@a).tar.gz": m1}


def test_merge_folder_manifests():
    my_device = DeviceID("b@b")
    other_device = DeviceID("a@a")
    parent = EntryID()
    v1 = LocalFolderManifest.new_placeholder(parent=parent).to_remote(author=other_device)

    # Initial base manifest
    m1 = LocalFolderManifest.from_remote(v1)
    assert merge_manifests(my_device, m1) == m1

    # Local change
    m2 = m1.evolve_children_and_mark_updated({"a": EntryID()})
    assert merge_manifests(my_device, m2) == m2

    # Successful upload
    v2 = m2.to_remote(author=my_device)
    m3 = merge_manifests(my_device, m2, v2)
    assert m3 == LocalFolderManifest.from_remote(v2)

    # Two local changes
    m4 = m3.evolve_children_and_mark_updated({"b": EntryID()})
    assert merge_manifests(my_device, m4) == m4
    m5 = m4.evolve_children_and_mark_updated({"c": EntryID()})
    assert merge_manifests(my_device, m4) == m4

    # M4 has been successfully uploaded
    v3 = m4.to_remote(author=my_device)
    m6 = merge_manifests(my_device, m5, v3)
    assert m6 == m5.evolve(base=v3)

    # The remote has changed
    v4 = v3.evolve(version=4, children={"d": EntryID(), **v3.children}, author=other_device)
    m7 = merge_manifests(my_device, m6, v4)
    assert m7.base_version == 4
    assert sorted(m7.children) == ["a", "b", "c", "d"]
    assert m7.need_sync

    # Successful upload
    v5 = m7.to_remote(author=my_device)
    m8 = merge_manifests(my_device, m7, v5)
    assert m8 == LocalFolderManifest.from_remote(v5)

    # The remote has changed
    v6 = v5.evolve(version=6, children={"e": EntryID(), **v5.children}, author=other_device)
    m9 = merge_manifests(my_device, m8, v6)
    assert m9 == LocalFolderManifest.from_remote(v6)


def test_merge_manifests_with_a_placeholder():
    my_device = DeviceID("b@b")
    other_device = DeviceID("a@a")
    parent = EntryID()

    m1 = LocalFolderManifest.new_placeholder(parent=parent)
    m2 = merge_manifests(my_device, m1)
    assert m2 == m1
    v1 = m1.to_remote(author=my_device)

    m2a = merge_manifests(my_device, m1, v1)
    assert m2a == LocalFolderManifest.from_remote(v1)

    m2b = m1.evolve_children_and_mark_updated({"a": EntryID()})
    m3b = merge_manifests(my_device, m2b, v1)
    assert m3b == m2b.evolve(base=v1)

    v2 = v1.evolve(version=2, author=other_device, children={"b": EntryID()})
    m2c = m1.evolve_children_and_mark_updated({"a": EntryID()})
    m3c = merge_manifests(my_device, m2c, v2)
    children = {**v2.children, **m2c.children}
    assert m3c == m2c.evolve(base=v2, children=children, updated=m3c.updated)


def test_merge_file_manifests():
    my_device = DeviceID("b@b")
    other_device = DeviceID("a@a")
    parent = EntryID()
    v1 = LocalFileManifest.new_placeholder(parent=parent).to_remote(author=other_device)

    def evolve(m, n):
        chunk = Chunk.new(0, n).evolve_as_block(b"a" * n)
        blocks = ((chunk,),)
        return m1.evolve_and_mark_updated(size=n, blocks=blocks)

    # Initial base manifest
    m1 = LocalFileManifest.from_remote(v1)
    assert merge_manifests(my_device, m1) == m1

    # Local change
    m2 = evolve(m1, 1)
    assert merge_manifests(my_device, m2) == m2

    # Successful upload
    v2 = m2.to_remote(author=my_device)
    m3 = merge_manifests(my_device, m2, v2)
    assert m3 == LocalFileManifest.from_remote(v2)

    # Two local changes
    m4 = evolve(m3, 2)
    assert merge_manifests(my_device, m4) == m4
    m5 = evolve(m4, 3)
    assert merge_manifests(my_device, m4) == m4

    # M4 has been successfully uploaded
    v3 = m4.to_remote(author=my_device)
    m6 = merge_manifests(my_device, m5, v3)
    assert m6 == m5.evolve(base=v3)

    # The remote has changed
    v4 = v3.evolve(version=4, size=0, author=other_device)
    with pytest.raises(FSFileConflictError):
        merge_manifests(my_device, m6, v4)


@pytest.mark.trio
@pytest.mark.parametrize("type", ["file", "folder"])
async def test_synchronization_step_transaction(alice_sync_transactions, type):
    sync_transactions = alice_sync_transactions
    synchronization_step = sync_transactions.synchronization_step
    entry_id = sync_transactions.get_workspace_entry().id

    # Sync a placeholder
    manifest = await synchronization_step(entry_id)

    # Acknowledge a successful synchronization
    assert await synchronization_step(entry_id, manifest) is None

    # Local change
    if type == "file":
        a_id, fd = await sync_transactions.file_create(FsPath("/a"))
        await sync_transactions.fd_write(fd, b"abc", 0)
        await sync_transactions.fd_close(fd)
    else:
        a_id = await sync_transactions.folder_create(FsPath("/a"))

    # Sync parent with a placeholder child
    manifest = await synchronization_step(entry_id)
    children = []
    async for child in sync_transactions.get_placeholder_children(manifest):
        children.append(child)
    a_entry_id, = children
    assert a_entry_id == a_id

    # Sync child
    if type == "file":
        await synchronization_step(a_entry_id)
    a_manifest = await synchronization_step(a_entry_id)
    assert await synchronization_step(a_entry_id, a_manifest) is None

    # Acknowledge the manifest
    assert sorted(manifest.children) == ["a"]
    assert await synchronization_step(entry_id, manifest) is None

    # Local change
    b_id = await sync_transactions.folder_create(FsPath("/b"))

    # Remote change
    children = {**manifest.children, "c": EntryID()}
    manifest = manifest.evolve(version=5, children=children, author="b@b")

    # Sync parent with a placeholder child
    manifest = await synchronization_step(entry_id, manifest)
    children = []
    async for child in sync_transactions.get_placeholder_children(manifest):
        children.append(child)
    b_entry_id, = children
    assert b_entry_id == b_id

    # Sync child
    b_manifest = await synchronization_step(b_entry_id)
    assert await synchronization_step(b_entry_id, b_manifest) is None

    # Acknowledge the manifest
    assert sorted(manifest.children) == ["a", "b", "c"]
    assert await synchronization_step(entry_id, manifest) is None


@pytest.mark.trio
async def test_get_minimal_remote_manifest(alice, alice_sync_transactions):
    sync_transactions = alice_sync_transactions

    # Prepare
    w_id = sync_transactions.workspace_id
    a_id, fd = await sync_transactions.file_create(FsPath("/a"))
    await sync_transactions.fd_write(fd, b"abc", 0)
    await sync_transactions.fd_close(fd)
    b_id = await sync_transactions.folder_create(FsPath("/b"))
    c_id = await sync_transactions.folder_create(FsPath("/b/c"))

    # Workspace manifest
    minimal = await sync_transactions.get_minimal_remote_manifest(w_id)
    local = await sync_transactions.local_storage.get_manifest(w_id)
    expected = local.to_remote(author=alice.device_id, timestamp=minimal.timestamp).evolve(
        children={}, updated=local.created
    )
    assert minimal == expected

    await sync_transactions.synchronization_step(w_id, minimal)
    assert await sync_transactions.get_minimal_remote_manifest(w_id) is None

    # File manifest
    minimal = await sync_transactions.get_minimal_remote_manifest(a_id)
    local = await sync_transactions.local_storage.get_manifest(a_id)
    expected = local.evolve(blocks=(), updated=local.created, size=0).to_remote(
        author=alice.device_id, timestamp=minimal.timestamp
    )
    assert minimal == expected
    await sync_transactions.file_reshape(a_id)
    await sync_transactions.synchronization_step(a_id, minimal)
    assert await sync_transactions.get_minimal_remote_manifest(a_id) is None

    # Folder manifest
    minimal = await sync_transactions.get_minimal_remote_manifest(b_id)
    local = await sync_transactions.local_storage.get_manifest(b_id)
    expected = local.to_remote(author=alice.device_id, timestamp=minimal.timestamp).evolve(
        children={}, updated=local.created
    )
    assert minimal == expected
    await sync_transactions.synchronization_step(b_id, minimal)
    assert await sync_transactions.get_minimal_remote_manifest(b_id) is None

    # Empty folder manifest
    minimal = await sync_transactions.get_minimal_remote_manifest(c_id)
    local = await sync_transactions.local_storage.get_manifest(c_id)
    expected = local.to_remote(author=alice.device_id, timestamp=minimal.timestamp)
    assert minimal == expected
    await sync_transactions.synchronization_step(c_id, minimal)
    assert await sync_transactions.get_minimal_remote_manifest(c_id) is None


@pytest.mark.trio
async def test_file_conflict(alice_sync_transactions):
    sync_transactions = alice_sync_transactions

    # Prepare
    a_id, fd = await sync_transactions.file_create(FsPath("/a"))
    await sync_transactions.fd_write(fd, b"abc", offset=0)
    await sync_transactions.file_reshape(a_id)
    remote = await sync_transactions.synchronization_step(a_id)
    assert await sync_transactions.synchronization_step(a_id, remote) is None
    await sync_transactions.fd_write(fd, b"def", offset=3)
    await sync_transactions.file_reshape(a_id)
    changed_remote = remote.evolve(version=2, blocks=[], size=0, author="b@b")

    # Try a synchronization
    with pytest.raises(FSFileConflictError) as ctx:
        await sync_transactions.synchronization_step(a_id, changed_remote)
    local, remote = ctx.value.args

    # Write some more
    await sync_transactions.fd_write(fd, b"ghi", offset=6)

    # Also create a fake previous conflict file
    await sync_transactions.file_create(FsPath("/a (conflicting with b@b)"), open=False)

    # Solve conflict
    with sync_transactions.event_bus.listen() as spy:
        await sync_transactions.file_conflict(a_id, local, remote)
    assert await sync_transactions.fd_read(fd, size=-1, offset=0) == b""
    a2_id, fd2 = await sync_transactions.file_open(FsPath("/a (conflicting with b@b - 2)"))
    assert await sync_transactions.fd_read(fd2, size=-1, offset=0) == b"abcdefghi"
    spy.assert_events_exactly_occured(
        [
            (
                CoreEvent.FS_ENTRY_UPDATED,
                {"workspace_id": sync_transactions.workspace_id, "id": a2_id},
            ),
            (
                CoreEvent.FS_ENTRY_UPDATED,
                {
                    "workspace_id": sync_transactions.workspace_id,
                    "id": sync_transactions.workspace_id,
                },
            ),
            (
                CoreEvent.FS_ENTRY_FILE_CONFLICT_RESOLVED,
                {"workspace_id": sync_transactions.workspace_id, "id": a_id, "backup_id": a2_id},
            ),
        ]
    )

    # Finish synchronization
    await sync_transactions.file_reshape(a2_id)
    assert await sync_transactions.synchronization_step(a_id, changed_remote) is None
    remote2 = await sync_transactions.synchronization_step(a2_id)
    assert await sync_transactions.synchronization_step(a2_id, remote2) is None

    # Create a new conflict then remove a
    await sync_transactions.fd_write(fd, b"abc", 0)
    await sync_transactions.file_reshape(a_id)
    changed_remote = changed_remote.evolve(version=3)
    await sync_transactions.file_delete(FsPath("/a"))

    # Conflict solving should still succeed
    with pytest.raises(FSFileConflictError) as ctx:
        await sync_transactions.synchronization_step(a_id, changed_remote)
    local, remote = ctx.value.args
    with sync_transactions.event_bus.listen() as spy:
        await sync_transactions.file_conflict(a_id, local, remote)
    spy.assert_events_exactly_occured([])

    # Close fds
    await sync_transactions.fd_close(fd)
    await sync_transactions.fd_close(fd2)
