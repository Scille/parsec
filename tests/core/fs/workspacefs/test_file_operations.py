# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Tuple

import pytest
from hypothesis import strategies
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

from parsec.core.fs.workspacefs.file_operations import (
    prepare_read,
    prepare_reshape,
    prepare_resize,
    prepare_write,
)
from parsec.core.fs.workspacefs.file_transactions import padded_data
from parsec.core.types import Chunk, ChunkID, EntryID, LocalFileManifest
from tests.common import freeze_time

MAX_SIZE = 64
size = strategies.integers(min_value=0, max_value=MAX_SIZE)


class Storage(dict):
    def read_chunk_data(self, chunk_id: ChunkID) -> bytes:
        return self[chunk_id]

    def write_chunk_data(self, chunk_id: ChunkID, data: bytes) -> None:
        assert chunk_id not in self
        self[chunk_id] = data

    def clear_chunk_data(self, chunk_id: ChunkID) -> None:
        self.pop(chunk_id)

    def read_chunk(self, chunk: Chunk) -> bytes:
        data = self.read_chunk_data(chunk.id)
        return data[chunk.start - chunk.raw_offset : chunk.stop - chunk.raw_offset]

    def write_chunk(self, chunk: Chunk, content: bytes, offset: int = 0) -> None:
        data = padded_data(content, offset, offset + chunk.stop - chunk.start)
        self.write_chunk_data(chunk.id, data)

    def build_data(self, chunks: Tuple[Chunk]) -> bytearray:
        # Empty array
        if not chunks:
            return bytearray()

        # Build byte array
        start, stop = chunks[0].start, chunks[-1].stop
        result = bytearray(stop - start)
        for chunk in chunks:
            result[chunk.start - start : chunk.stop - start] = self.read_chunk(chunk)

        # Return byte array
        return result

    # File operations

    def read(self, manifest: LocalFileManifest, size: int, offset: int) -> bytearray:
        chunks = prepare_read(manifest, size, offset)
        return self.build_data(chunks)

    def write(self, manifest: LocalFileManifest, content: bytes, offset: int) -> LocalFileManifest:
        # No-op
        if len(content) == 0:
            return manifest
        # Write
        manifest, write_operations, removed_ids = prepare_write(manifest, len(content), offset)
        for chunk, offset in write_operations:
            self.write_chunk(chunk, content, offset)
        for removed_id in removed_ids:
            self.clear_chunk_data(removed_id)
        return manifest

    def resize(self, manifest: LocalFileManifest, size: int) -> LocalFileManifest:
        # No-op
        if size == manifest.size:
            return manifest
        # Resize
        new_manifest, write_operations, removed_ids = prepare_resize(manifest, size)
        for chunk, offset in write_operations:
            self.write_chunk(chunk, b"", offset)
        for removed_id in removed_ids:
            self.clear_chunk_data(removed_id)
        return new_manifest

    def reshape(self, manifest: LocalFileManifest) -> LocalFileManifest:

        for source, destination, update, removed_ids in prepare_reshape(manifest):
            data = self.build_data(source)
            new_chunk = destination.evolve_as_block(data)
            if source != (destination,):
                self.write_chunk(new_chunk, data)
            manifest = update(manifest, new_chunk)
            for removed_id in removed_ids:
                self.clear_chunk_data(removed_id)

        return manifest


def test_complete_scenario():
    storage = Storage()

    with freeze_time("2000-01-01"):
        base = manifest = LocalFileManifest.new_placeholder(parent=EntryID(), blocksize=16)
        assert manifest == base.evolve(size=0)

    with freeze_time("2000-01-02") as t2:
        manifest = storage.write(manifest, b"Hello ", 0)
        assert storage.read(manifest, 6, 0) == b"Hello "

    (chunk0,), = manifest.blocks
    assert manifest == base.evolve(size=6, blocks=((chunk0,),), updated=t2)
    assert chunk0 == Chunk(id=chunk0.id, start=0, stop=6, raw_offset=0, raw_size=6, access=None)
    assert storage[chunk0.id] == b"Hello "

    with freeze_time("2000-01-03") as t3:
        manifest = storage.write(manifest, b"world !", 6)
        assert storage.read(manifest, 13, 0) == b"Hello world !"

    (_, chunk1), = manifest.blocks
    assert manifest == base.evolve(size=13, blocks=((chunk0, chunk1),), updated=t3)
    assert chunk1 == Chunk(id=chunk1.id, start=6, stop=13, raw_offset=6, raw_size=7, access=None)
    assert storage[chunk1.id] == b"world !"

    with freeze_time("2000-01-04") as t4:
        manifest = storage.write(manifest, b"\n More kontent", 13)
        assert storage.read(manifest, 27, 0) == b"Hello world !\n More kontent"

    (_, _, chunk2), (chunk3,) = manifest.blocks
    assert storage[chunk2.id] == b"\n M"
    assert storage[chunk3.id] == b"ore kontent"
    assert manifest == base.evolve(
        size=27, blocks=((chunk0, chunk1, chunk2), (chunk3,)), updated=t4
    )

    with freeze_time("2000-01-05") as t5:
        manifest = storage.write(manifest, b"c", 20)
        assert storage.read(manifest, 27, 0) == b"Hello world !\n More content"

    chunk4, chunk5, chunk6 = manifest.blocks[1]
    assert chunk3.id == chunk4.id == chunk6.id
    assert storage[chunk5.id] == b"c"
    assert manifest == base.evolve(
        size=27, blocks=((chunk0, chunk1, chunk2), (chunk4, chunk5, chunk6)), updated=t5
    )

    with freeze_time("2000-01-06") as t6:
        manifest = storage.resize(manifest, 40)
        expected = b"Hello world !\n More content" + b"\x00" * 13
        assert storage.read(manifest, 40, 0) == expected

    (_, _, _, chunk7), (chunk8,) = manifest.blocks[1:]
    assert storage[chunk7.id] == b"\x00" * 5
    assert storage[chunk8.id] == b"\x00" * 8
    assert manifest == base.evolve(
        size=40,
        blocks=((chunk0, chunk1, chunk2), (chunk4, chunk5, chunk6, chunk7), (chunk8,)),
        updated=t6,
    )

    with freeze_time("2000-01-07") as t7:
        manifest = storage.resize(manifest, 25)
        expected = b"Hello world !\n More conte"
        assert storage.read(manifest, 25, 0) == expected

    (_, _, chunk9), = manifest.blocks[1:]
    assert chunk9.id == chunk6.id
    assert manifest == base.evolve(
        size=25, blocks=((chunk0, chunk1, chunk2), (chunk4, chunk5, chunk9)), updated=t7
    )

    with freeze_time("2000-01-08"):
        assert not manifest.is_reshaped()
        manifest = storage.reshape(manifest)
        expected = b"Hello world !\n More conte"
        assert storage.read(manifest, 25, 0) == expected
        assert manifest.is_reshaped()

    (chunk10,), (chunk11,) = manifest.blocks
    assert storage[chunk10.id] == b"Hello world !\n M"
    assert storage[chunk11.id] == b"ore conte"
    assert manifest == base.evolve(size=25, blocks=((chunk10,), (chunk11,)), updated=t7)


@pytest.mark.slow
def test_file_operations(hypothesis_settings, tmpdir):
    class FileOperations(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.oracle = open(tmpdir / "oracle.txt", "w+b")
            self.manifest = LocalFileManifest.new_placeholder(parent=EntryID(), blocksize=8)
            self.storage = Storage()

        def teardown(self) -> None:
            self.oracle.close()
            self.storage.clear()

        @invariant()
        def integrity(self) -> None:
            self.manifest.assert_integrity()

        @invariant()
        def leaks(self) -> None:
            all_ids = {chunk.id for chunks in self.manifest.blocks for chunk in chunks}
            assert set(self.storage) == all_ids

        @rule(size=size, offset=size)
        def read(self, size: int, offset: int) -> None:
            data = self.storage.read(self.manifest, size, offset)
            self.oracle.seek(offset)
            expected = self.oracle.read(size)
            assert data == expected

        @rule(content=strategies.binary(max_size=MAX_SIZE), offset=size)
        def write(self, content: bytes, offset: int) -> None:
            self.manifest = self.storage.write(self.manifest, content, offset)
            self.oracle.seek(offset)
            self.oracle.write(content)

        @rule(length=size)
        def resize(self, length: int) -> None:
            self.manifest = self.storage.resize(self.manifest, length)
            self.oracle.truncate(length)

        @rule()
        def reshape(self) -> None:
            self.manifest = self.storage.reshape(self.manifest)
            assert self.manifest.is_reshaped()

    run_state_machine_as_test(FileOperations, settings=hypothesis_settings)
