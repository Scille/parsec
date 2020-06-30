# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pathlib import Path

import pytest

from parsec.core.fs.storage import UserStorage
from parsec.core.types import EntryID, LocalUserManifest


@pytest.fixture
def user_manifest(alice):
    return LocalUserManifest.new_placeholder(id=alice.user_manifest_id)


@pytest.fixture
async def alice_user_storage(tmpdir, alice):
    async with UserStorage.run(alice, tmpdir) as aus:
        yield aus


@pytest.mark.trio
async def test_initialization(alice, alice_user_storage, user_manifest):
    aus = alice_user_storage

    # Brand new user storage contains user manifest placeholder
    user_manifest_v0 = aus.get_user_manifest()
    assert user_manifest_v0.id == alice.user_manifest_id
    assert user_manifest_v0.need_sync
    assert user_manifest_v0.base.author is None
    assert user_manifest_v0.base.id == alice.user_manifest_id
    assert user_manifest_v0.base.version == 0

    await aus.set_user_manifest(user_manifest)
    assert aus.get_user_manifest() == user_manifest
    async with UserStorage.run(aus.device, aus.path) as aus2:
        assert aus2.get_user_manifest() == user_manifest

    new_user_manifest = user_manifest.evolve(need_sync=False)
    await aus.set_user_manifest(new_user_manifest)
    assert aus.get_user_manifest() == new_user_manifest
    async with UserStorage.run(aus.device, aus.path) as aus2:
        assert aus2.get_user_manifest() == new_user_manifest


@pytest.mark.trio
async def test_realm_checkpoint(alice, alice_user_storage, initial_user_manifest_state):
    aws = alice_user_storage
    user_manifest_id = alice.user_manifest_id

    # Brand new user storage contains user manifest placeholder
    assert await aws.get_realm_checkpoint() == 0
    assert await aws.get_need_sync_entries() == ({user_manifest_id}, set())

    # Modified entries not in storage should be ignored
    await aws.update_realm_checkpoint(1, {EntryID(): 2})
    assert await aws.get_realm_checkpoint() == 1
    assert await aws.get_need_sync_entries() == ({user_manifest_id}, set())

    # Another device updated the user manifest remotly
    await aws.update_realm_checkpoint(2, {user_manifest_id: 1})
    assert await aws.get_realm_checkpoint() == 2
    assert await aws.get_need_sync_entries() == ({user_manifest_id}, {user_manifest_id})

    # Load the up to date version of the user manifest
    manifest_v1 = initial_user_manifest_state.get_user_manifest_v1_for_device(alice)
    await aws.set_user_manifest(manifest_v1)
    assert await aws.get_realm_checkpoint() == 2
    assert await aws.get_need_sync_entries() == (set(), set())

    # Provide new remote changes
    await aws.update_realm_checkpoint(3, {user_manifest_id: 2})
    assert await aws.get_realm_checkpoint() == 3
    assert await aws.get_need_sync_entries() == (set(), {user_manifest_id})

    # Provide new local changes too
    manifest_v1_modified = manifest_v1.evolve(need_sync=True)
    await aws.set_user_manifest(manifest_v1_modified)
    assert await aws.get_realm_checkpoint() == 3
    assert await aws.get_need_sync_entries() == ({user_manifest_id}, {user_manifest_id})

    # Checkpoint's remote version should be ignored if manifest base version is greater
    manifest_v2 = manifest_v1_modified.evolve(base=manifest_v1.base.evolve(version=4))
    await aws.set_user_manifest(manifest_v2)
    assert await aws.get_realm_checkpoint() == 3
    assert await aws.get_need_sync_entries() == ({user_manifest_id}, set())


@pytest.mark.trio
async def test_vacuum(alice_user_storage):
    # Should be no-op
    await alice_user_storage.run_vacuum()


@pytest.mark.trio
async def test_storage_file_tree(tmpdir, alice):
    path = Path(tmpdir)
    manifest_sqlite_db = path / "user_data-v1.sqlite"

    async with UserStorage.run(alice, tmpdir) as aus:
        assert aus.manifest_storage.path == manifest_sqlite_db

    assert set(path.iterdir()) == {manifest_sqlite_db}
