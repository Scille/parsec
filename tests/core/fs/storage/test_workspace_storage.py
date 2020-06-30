# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pathlib import Path

import pytest
from pendulum import now

from parsec.core.fs import FSError, FSInvalidFileDescriptor
from parsec.core.fs.exceptions import FSLocalMissError
from parsec.core.fs.storage import WorkspaceStorage
from parsec.core.types import (
    DEFAULT_BLOCK_SIZE,
    Chunk,
    EntryID,
    LocalFileManifest,
    LocalFolderManifest,
    LocalUserManifest,
    LocalWorkspaceManifest,
)


def create_manifest(device, type=LocalWorkspaceManifest):
    if type is LocalUserManifest:
        manifest = LocalUserManifest.new_placeholder(parent=EntryID())
    elif type is LocalWorkspaceManifest:
        manifest = type.new_placeholder()
    else:
        manifest = type.new_placeholder(parent=EntryID())
    return manifest


@pytest.fixture
def workspace_id():
    return EntryID()


@pytest.fixture
async def alice_workspace_storage(tmpdir, alice, workspace_id):
    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:
        yield aws


@pytest.mark.trio
async def test_lock_required(alice_workspace_storage):
    manifest = create_manifest(alice_workspace_storage.device)
    msg = f"Entry `{manifest.id}` modified without beeing locked"

    with pytest.raises(RuntimeError) as exc:
        await alice_workspace_storage.set_manifest(manifest.id, manifest)
    assert str(exc.value) == msg

    with pytest.raises(RuntimeError) as exc:
        await alice_workspace_storage.ensure_manifest_persistent(manifest.id)
    assert str(exc.value) == msg

    with pytest.raises(RuntimeError) as exc:
        await alice_workspace_storage.clear_manifest(manifest.id)
    assert str(exc.value) == msg

    # Note: `get_manifest` doesn't need a lock before use


@pytest.mark.trio
async def test_basic_set_get_clear(alice_workspace_storage):
    aws = alice_workspace_storage
    manifest = create_manifest(aws.device)
    async with aws.lock_entry_id(manifest.id):

        # 1) No data
        with pytest.raises(FSLocalMissError):
            await aws.get_manifest(manifest.id)

        # 2) Set data
        await aws.set_manifest(manifest.id, manifest)
        assert await aws.get_manifest(manifest.id) == manifest
        # Make sure data are not only stored in cache
        async with WorkspaceStorage.run(aws.device, aws.path, aws.workspace_id) as aws2:
            assert await aws2.get_manifest(manifest.id) == manifest

        # 3) Clear data
        await aws.clear_manifest(manifest.id)
        with pytest.raises(FSLocalMissError):
            await aws.get_manifest(manifest.id)
        with pytest.raises(FSLocalMissError):
            await aws.clear_manifest(manifest.id)

        async with WorkspaceStorage.run(aws.device, aws.path, aws.workspace_id) as aws3:
            with pytest.raises(FSLocalMissError):
                assert await aws3.get_manifest(manifest.id) == manifest


@pytest.mark.trio
async def test_cache_set_get(tmpdir, alice, workspace_id):
    manifest = create_manifest(alice)

    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:

        async with aws.lock_entry_id(manifest.id):

            # 1) Set data
            await aws.set_manifest(manifest.id, manifest, cache_only=True)
            assert await aws.get_manifest(manifest.id) == manifest
            async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws2:
                with pytest.raises(FSLocalMissError):
                    await aws2.get_manifest(manifest.id)

            # 2) Clear should work as expected
            await aws.clear_manifest(manifest.id)
            with pytest.raises(FSLocalMissError):
                await aws.get_manifest(manifest.id)

            # 3) Re-set data
            await aws.set_manifest(manifest.id, manifest, cache_only=True)
            assert await aws.get_manifest(manifest.id) == manifest
            async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws3:
                with pytest.raises(FSLocalMissError):
                    await aws3.get_manifest(manifest.id)

            # 4) Flush data
            await aws.ensure_manifest_persistent(manifest.id)
            assert await aws.get_manifest(manifest.id) == manifest
            async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws4:
                assert await aws4.get_manifest(manifest.id) == manifest

            # 5) Idempotency
            await aws.ensure_manifest_persistent(manifest.id)


@pytest.mark.trio
@pytest.mark.parametrize("cache_only", (False, True))
@pytest.mark.parametrize("clear_manifest", (False, True))
async def test_chunk_clearing(alice_workspace_storage, cache_only, clear_manifest):
    aws = alice_workspace_storage
    manifest = create_manifest(aws.device, LocalFileManifest)
    data1 = b"abc"
    chunk1 = Chunk.new(0, 3)
    data2 = b"def"
    chunk2 = Chunk.new(3, 6)
    manifest = manifest.evolve(blocks=((chunk1, chunk2),), size=6)

    async with aws.lock_entry_id(manifest.id):
        # Set chunks and manifests
        await aws.set_chunk(chunk1.id, data1)
        await aws.set_chunk(chunk2.id, data2)
        await aws.set_manifest(manifest.id, manifest)

        # Set a new version of the manifest without the chunks
        removed_ids = {chunk1.id, chunk2.id}
        new_manifest = manifest.evolve(blocks=())
        await aws.set_manifest(
            manifest.id, new_manifest, cache_only=cache_only, removed_ids=removed_ids
        )

        # The chunks are still accessible
        if cache_only:
            await aws.get_chunk(chunk1.id) == b"abc"
            await aws.get_chunk(chunk2.id) == b"def"

        # The chunks are gone
        else:
            with pytest.raises(FSLocalMissError):
                await aws.get_chunk(chunk1.id)
            with pytest.raises(FSLocalMissError):
                await aws.get_chunk(chunk2.id)

        # Now flush the manifest
        if clear_manifest:
            await aws.clear_manifest(manifest.id)
        else:
            await aws.ensure_manifest_persistent(manifest.id)

        # The chunks are gone
        with pytest.raises(FSLocalMissError):
            await aws.get_chunk(chunk1.id)
        with pytest.raises(FSLocalMissError):
            await aws.get_chunk(chunk2.id)

        # Idempotency
        await aws.manifest_storage._ensure_manifest_persistent(manifest.id)


@pytest.mark.trio
async def test_cache_flushed_on_exit(tmpdir, alice, workspace_id):
    manifest = create_manifest(alice)

    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:
        async with aws.lock_entry_id(manifest.id):
            await aws.set_manifest(manifest.id, manifest, cache_only=True)

    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws2:
        assert await aws2.get_manifest(manifest.id) == manifest


@pytest.mark.trio
async def test_clear_cache(alice_workspace_storage):
    aws = alice_workspace_storage
    manifest1 = create_manifest(aws.device)
    manifest2 = create_manifest(aws.device)

    # Set manifest 1 and manifest 2, cache only
    async with aws.lock_entry_id(manifest1.id):
        await aws.set_manifest(manifest1.id, manifest1)
    async with aws.lock_entry_id(manifest2.id):
        await aws.set_manifest(manifest2.id, manifest2, cache_only=True)

    # Clear without flushing
    await aws.clear_memory_cache(flush=False)

    # Manifest 1 is present but manifest 2 got lost
    assert await aws.get_manifest(manifest1.id) == manifest1
    with pytest.raises(FSLocalMissError):
        await aws.get_manifest(manifest2.id)

    # Set manifest 2, cache only
    async with aws.lock_entry_id(manifest2.id):
        await aws.set_manifest(manifest2.id, manifest2, cache_only=True)

    # Clear with flushing
    await aws.clear_memory_cache()

    # Manifest 2 is present
    assert await aws.get_manifest(manifest2.id) == manifest2


@pytest.mark.parametrize("type", [LocalWorkspaceManifest, LocalFolderManifest, LocalFileManifest])
@pytest.mark.trio
async def test_serialize_types(tmpdir, alice, workspace_id, type):
    manifest = create_manifest(alice, type)
    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:
        async with aws.lock_entry_id(manifest.id):
            await aws.set_manifest(manifest.id, manifest)

    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws2:
        assert await aws2.get_manifest(manifest.id) == manifest


@pytest.mark.trio
async def test_serialize_non_empty_local_file_manifest(tmpdir, alice, workspace_id):
    manifest = create_manifest(alice, LocalFileManifest)
    chunk1 = Chunk.new(0, 7).evolve_as_block(b"0123456")
    chunk2 = Chunk.new(7, 8)
    chunk3 = Chunk.new(8, 10)
    blocks = (chunk1, chunk2), (chunk3,)
    manifest = manifest.evolve_and_mark_updated(blocksize=8, size=10, blocks=blocks)
    manifest.assert_integrity()
    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:
        async with aws.lock_entry_id(manifest.id):
            await aws.set_manifest(manifest.id, manifest)

    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws2:
        assert await aws2.get_manifest(manifest.id) == manifest


@pytest.mark.trio
async def test_realm_checkpoint(alice_workspace_storage):
    aws = alice_workspace_storage
    manifest = create_manifest(aws.device, LocalFileManifest)

    assert await aws.get_realm_checkpoint() == 0
    assert await aws.get_need_sync_entries() == (set(), set())

    await aws.update_realm_checkpoint(11, {manifest.id: 22, EntryID(): 33})

    assert await aws.get_realm_checkpoint() == 11
    assert await aws.get_need_sync_entries() == (set(), set())

    await aws.set_manifest(manifest.id, manifest, check_lock_status=False)

    assert await aws.get_realm_checkpoint() == 11
    assert await aws.get_need_sync_entries() == (set([manifest.id]), set())

    await aws.set_manifest(manifest.id, manifest.evolve(need_sync=False), check_lock_status=False)

    assert await aws.get_realm_checkpoint() == 11
    assert await aws.get_need_sync_entries() == (set(), set())

    await aws.update_realm_checkpoint(44, {manifest.id: 55, EntryID(): 66})

    assert await aws.get_realm_checkpoint() == 44
    assert await aws.get_need_sync_entries() == (set(), set([manifest.id]))


@pytest.mark.trio
async def test_lock_manifest(tmpdir, alice, workspace_id):
    manifest = create_manifest(alice, LocalFileManifest)
    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:

        with pytest.raises(FSLocalMissError):
            async with aws.lock_manifest(manifest.id):
                pass

        await aws.set_manifest(manifest.id, manifest, check_lock_status=False)

        async with aws.lock_manifest(manifest.id) as m1:
            assert m1 == manifest
            m2 = manifest.evolve(need_sync=False)
            await aws.set_manifest(manifest.id, m2)
            assert await aws.get_manifest(manifest.id) == m2


@pytest.mark.trio
async def test_block_interface(alice_workspace_storage):
    data = b"0123456"
    aws = alice_workspace_storage

    chunk = Chunk.new(0, 7).evolve_as_block(data)
    block_id = chunk.access.id
    await aws.clear_clean_block(block_id)
    with pytest.raises(FSLocalMissError):
        await aws.get_chunk(chunk.id)
    assert not await aws.block_storage.is_chunk(chunk.id)
    assert await aws.block_storage.get_total_size() == 0

    await aws.set_clean_block(block_id, data)
    assert await aws.get_chunk(chunk.id) == data
    assert await aws.block_storage.is_chunk(chunk.id)
    assert await aws.block_storage.get_total_size() >= 7

    await aws.clear_clean_block(block_id)
    await aws.clear_clean_block(block_id)
    with pytest.raises(FSLocalMissError):
        await aws.get_chunk(chunk.id)
    assert not await aws.block_storage.is_chunk(chunk.id)
    assert await aws.block_storage.get_total_size() == 0

    await aws.set_chunk(chunk.id, data)
    assert await aws.get_dirty_block(block_id) == data


@pytest.mark.trio
async def test_chunk_interface(alice_workspace_storage):
    data = b"0123456"
    aws = alice_workspace_storage
    chunk = Chunk.new(0, 7)

    with pytest.raises(FSLocalMissError):
        await aws.get_chunk(chunk.id)
    with pytest.raises(FSLocalMissError):
        await aws.clear_chunk(chunk.id)
    await aws.clear_chunk(chunk.id, miss_ok=True)
    assert not await aws.chunk_storage.is_chunk(chunk.id)
    assert await aws.chunk_storage.get_total_size() == 0

    await aws.set_chunk(chunk.id, data)
    assert await aws.get_chunk(chunk.id) == data
    assert await aws.chunk_storage.is_chunk(chunk.id)
    assert await aws.chunk_storage.get_total_size() >= 7

    await aws.clear_chunk(chunk.id)
    with pytest.raises(FSLocalMissError):
        await aws.get_chunk(chunk.id)
    with pytest.raises(FSLocalMissError):
        await aws.clear_chunk(chunk.id)
    assert not await aws.chunk_storage.is_chunk(chunk.id)
    assert await aws.chunk_storage.get_total_size() == 0
    await aws.clear_chunk(chunk.id, miss_ok=True)


@pytest.mark.trio
async def test_file_descriptor(alice_workspace_storage):
    aws = alice_workspace_storage
    manifest = create_manifest(aws.device, LocalFileManifest)
    await aws.set_manifest(manifest.id, manifest, check_lock_status=False)

    fd = aws.create_file_descriptor(manifest)
    assert fd == 1

    assert await aws.load_file_descriptor(fd) == manifest

    aws.remove_file_descriptor(fd)
    with pytest.raises(FSInvalidFileDescriptor):
        await aws.load_file_descriptor(fd)
    with pytest.raises(FSInvalidFileDescriptor):
        aws.remove_file_descriptor(fd)


@pytest.mark.trio
async def test_run_vacuum(alice_workspace_storage):
    # Should be a no-op
    await alice_workspace_storage.run_vacuum()


@pytest.mark.trio
async def test_timestamped_storage(alice_workspace_storage):
    timestamp = now()
    aws = alice_workspace_storage
    taws = aws.to_timestamped(timestamp)
    assert taws.timestamp == timestamp
    assert taws.device == aws.device
    assert taws.path == aws.path
    assert taws.workspace_id == aws.workspace_id
    assert taws.manifest_storage is None
    assert taws.block_storage == aws.block_storage
    assert taws.chunk_storage == aws.chunk_storage

    with pytest.raises(FSError):
        await taws.set_chunk("chunk id", "data")

    with pytest.raises(FSError):
        await taws.clear_chunk("chunk id")

    with pytest.raises(FSError):
        await taws.clear_manifest("manifest id")

    with pytest.raises(FSLocalMissError):
        await taws.get_manifest(EntryID())

    manifest = create_manifest(aws.device)
    with pytest.raises(FSError):
        await taws.set_manifest(manifest.id, manifest)

    manifest = manifest.evolve(need_sync=False)
    async with taws.lock_entry_id(manifest.id):
        await taws.set_manifest(manifest.id, manifest)
    assert await taws.get_manifest(manifest.id) == manifest

    # No-op
    await taws.ensure_manifest_persistent(manifest.id)


@pytest.mark.trio
async def test_internal_connections(tmpdir, alice, workspace_id):
    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:
        with pytest.raises(RuntimeError):
            await aws.data_localdb._connect()
        with pytest.raises(RuntimeError):
            await aws.cache_localdb._connect()

    # Idempotency
    await aws.data_localdb._close()
    await aws.cache_localdb._close()


@pytest.mark.trio
async def test_vacuum(tmpdir, alice, workspace_id):
    data_size = 1 * 1024 * 1024
    chunk = Chunk.new(0, data_size)
    async with WorkspaceStorage.run(
        alice, tmpdir, workspace_id, vacuum_threshold=data_size // 2
    ) as aws:

        # Make sure the storage is empty
        data = b"\x00" * data_size
        assert aws.data_localdb.get_disk_usage() < data_size

        # Set and commit a chunk of 1MB
        await aws.set_chunk(chunk.id, data)
        await aws.data_localdb.commit()
        assert aws.data_localdb.get_disk_usage() > data_size

        # Run the vacuum
        await aws.run_vacuum()
        assert aws.data_localdb.get_disk_usage() > data_size

        # Clear the chunk 1MB
        await aws.clear_chunk(chunk.id)
        await aws.data_localdb.commit()
        assert aws.data_localdb.get_disk_usage() > data_size

        # Run the vacuum
        await aws.run_vacuum()
        assert aws.data_localdb.get_disk_usage() < data_size

        # Make sure vacuum can run even if a transaction has started
        await aws.set_chunk(chunk.id, data)
        await aws.run_vacuum()
        await aws.clear_chunk(chunk.id)
        await aws.run_vacuum()

        # Vacuuming the cache storage is no-op
        await aws.cache_localdb.run_vacuum()

    # Make sure disk usage can be called on a closed storage
    assert aws.data_localdb.get_disk_usage() < data_size


@pytest.mark.trio
async def test_garbage_collection(tmpdir, alice, workspace_id):
    block_size = DEFAULT_BLOCK_SIZE
    cache_size = 1 * block_size
    data = b"\x00" * block_size
    chunk1 = Chunk.new(0, block_size).evolve_as_block(data)
    chunk2 = Chunk.new(0, block_size).evolve_as_block(data)
    chunk3 = Chunk.new(0, block_size).evolve_as_block(data)

    async with WorkspaceStorage.run(alice, tmpdir, workspace_id, cache_size=cache_size) as aws:
        assert await aws.block_storage.get_nb_blocks() == 0
        await aws.set_clean_block(chunk1.access.id, data)
        assert await aws.block_storage.get_nb_blocks() == 1
        await aws.set_clean_block(chunk2.access.id, data)
        assert await aws.block_storage.get_nb_blocks() == 1
        await aws.set_clean_block(chunk3.access.id, data)
        assert await aws.block_storage.get_nb_blocks() == 1
        await aws.block_storage.clear_all_blocks()
        assert await aws.block_storage.get_nb_blocks() == 0


@pytest.mark.trio
async def test_storage_file_tree(alice, tmpdir, workspace_id):
    path = Path(tmpdir)
    manifest_sqlite_db = path / "workspace_data-v1.sqlite"
    chunk_sqlite_db = path / "workspace_data-v1.sqlite"
    block_sqlite_db = path / "workspace_cache-v1.sqlite"

    async with WorkspaceStorage.run(alice, tmpdir, workspace_id) as aws:
        assert aws.manifest_storage.path == manifest_sqlite_db
        assert aws.chunk_storage.path == chunk_sqlite_db
        assert aws.block_storage.path == block_sqlite_db

    assert set(path.iterdir()) == {manifest_sqlite_db, chunk_sqlite_db, block_sqlite_db}
