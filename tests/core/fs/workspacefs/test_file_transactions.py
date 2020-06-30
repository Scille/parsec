# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
from pathlib import Path

import pytest
from hypothesis import strategies as st
from hypothesis_trio.stateful import (
    TrioAsyncioRuleBasedStateMachine,
    initialize,
    rule,
    run_state_machine_as_test,
)
from pendulum import Pendulum

from parsec.core.fs.exceptions import FSRemoteBlockNotFound
from parsec.core.fs.storage import WorkspaceStorage
from parsec.core.fs.workspacefs.file_transactions import FSInvalidFileDescriptor
from parsec.core.types import Chunk, EntryID, LocalFileManifest
from tests.common import call_with_control, freeze_time


class File:
    def __init__(self, local_storage, manifest):
        self.fresh_manifest = manifest
        self.entry_id = manifest.id
        self.local_storage = local_storage

    def ensure_manifest(self, **kwargs):
        manifest = self.local_storage.manifest_storage._cache[self.entry_id]
        for k, v in kwargs.items():
            assert getattr(manifest, k) == v

    def is_cache_ahead_of_persistance(self):
        return self.entry_id in self.local_storage.manifest_storage._cache_ahead_of_localdb

    async def get_manifest(self):
        return await self.local_storage.get_manifest(self.entry_id)

    async def set_manifest(self, manifest):
        async with self.local_storage.lock_manifest(self.entry_id):
            await self.local_storage.set_manifest(self.entry_id, manifest)

    def open(self):
        return self.local_storage.create_file_descriptor(self.fresh_manifest)


@pytest.fixture
async def foo_txt(alice, alice_file_transactions):
    local_storage = alice_file_transactions.local_storage
    now = Pendulum(2000, 1, 2)
    placeholder = LocalFileManifest.new_placeholder(parent=EntryID(), now=now)
    remote_v1 = placeholder.to_remote(author=alice.device_id, timestamp=now)
    manifest = LocalFileManifest.from_remote(remote_v1)
    async with local_storage.lock_entry_id(manifest.id):
        await local_storage.set_manifest(manifest.id, manifest)
    return File(local_storage, manifest)


@pytest.mark.trio
async def test_close_unknown_fd(alice_file_transactions):
    with pytest.raises(FSInvalidFileDescriptor):
        await alice_file_transactions.fd_close(42)


@pytest.mark.trio
async def test_operations_on_file(alice_file_transactions, foo_txt):
    file_transactions = alice_file_transactions

    fd = foo_txt.open()
    assert isinstance(fd, int)

    with freeze_time("2000-01-03"):
        await file_transactions.fd_write(fd, b"hello ", 0)
        await file_transactions.fd_write(fd, b"world !", -1)
        await file_transactions.fd_write(fd, b"H", 0)
        await file_transactions.fd_write(fd, b"", 0)
        assert foo_txt.is_cache_ahead_of_persistance()

        fd2 = foo_txt.open()

        await file_transactions.fd_write(fd2, b"!!!", -1)
        data = await file_transactions.fd_read(fd2, 1, 0)
        assert data == b"H"

        await file_transactions.fd_close(fd2)
    foo_txt.ensure_manifest(
        size=16,
        is_placeholder=False,
        need_sync=True,
        base_version=1,
        created=Pendulum(2000, 1, 2),
        updated=Pendulum(2000, 1, 3),
    )

    data = await file_transactions.fd_read(fd, 5, 6)
    assert data == b"world"

    await file_transactions.fd_close(fd)
    assert not foo_txt.is_cache_ahead_of_persistance()

    fd2 = foo_txt.open()

    data = await file_transactions.fd_read(fd2, -1, 0)
    assert data == b"Hello world !!!!"

    await file_transactions.fd_close(fd2)

    assert not foo_txt.is_cache_ahead_of_persistance()
    foo_txt.ensure_manifest(
        size=16,
        is_placeholder=False,
        need_sync=True,
        base_version=1,
        created=Pendulum(2000, 1, 2),
        updated=Pendulum(2000, 1, 3),
    )


@pytest.mark.trio
async def test_flush_file(alice_file_transactions, foo_txt):
    file_transactions = alice_file_transactions

    fd = foo_txt.open()

    foo_txt.ensure_manifest(
        size=0,
        is_placeholder=False,
        need_sync=False,
        base_version=1,
        created=Pendulum(2000, 1, 2),
        updated=Pendulum(2000, 1, 2),
    )

    with freeze_time("2000-01-03"):
        await file_transactions.fd_write(fd, b"hello ", 0)
        await file_transactions.fd_write(fd, b"world !", -1)

    assert foo_txt.is_cache_ahead_of_persistance()
    foo_txt.ensure_manifest(
        size=13,
        is_placeholder=False,
        need_sync=True,
        base_version=1,
        created=Pendulum(2000, 1, 2),
        updated=Pendulum(2000, 1, 3),
    )

    await file_transactions.fd_flush(fd)
    assert not foo_txt.is_cache_ahead_of_persistance()

    await file_transactions.fd_close(fd)
    assert not foo_txt.is_cache_ahead_of_persistance()
    foo_txt.ensure_manifest(
        size=13,
        is_placeholder=False,
        need_sync=True,
        base_version=1,
        created=Pendulum(2000, 1, 2),
        updated=Pendulum(2000, 1, 3),
    )


@pytest.mark.trio
async def test_block_not_loaded_entry(alice_file_transactions, foo_txt):
    file_transactions = alice_file_transactions

    foo_manifest = await foo_txt.get_manifest()
    chunk1_data = b"a" * 10
    chunk2_data = b"b" * 5
    chunk1 = Chunk.new(0, 10).evolve_as_block(chunk1_data)
    chunk2 = Chunk.new(10, 15).evolve_as_block(chunk2_data)
    foo_manifest = foo_manifest.evolve(blocks=((chunk1, chunk2),), size=15)
    async with file_transactions.local_storage.lock_entry_id(foo_manifest.parent):
        await foo_txt.set_manifest(foo_manifest)

    fd = foo_txt.open()
    with pytest.raises(FSRemoteBlockNotFound):
        await file_transactions.fd_read(fd, 14, 0)

    await file_transactions.local_storage.set_chunk(chunk1.id, chunk1_data)
    await file_transactions.local_storage.set_chunk(chunk2.id, chunk2_data)

    data = await file_transactions.fd_read(fd, 14, 0)
    assert data == chunk1_data + chunk2_data[:4]


@pytest.mark.trio
async def test_load_block_from_remote(alice_file_transactions, foo_txt):
    file_transactions = alice_file_transactions

    # Prepare the backend
    workspace_id = file_transactions.remote_loader.workspace_id
    await file_transactions.remote_loader.create_realm(workspace_id)

    foo_manifest = await foo_txt.get_manifest()
    chunk1_data = b"a" * 10
    chunk2_data = b"b" * 5
    chunk1 = Chunk.new(0, 10).evolve_as_block(chunk1_data)
    chunk2 = Chunk.new(10, 15).evolve_as_block(chunk2_data)
    foo_manifest = foo_manifest.evolve(blocks=((chunk1, chunk2),), size=15)
    await foo_txt.set_manifest(foo_manifest)

    fd = foo_txt.open()
    await file_transactions.remote_loader.upload_block(chunk1.access, chunk1_data)
    await file_transactions.remote_loader.upload_block(chunk2.access, chunk2_data)
    await file_transactions.local_storage.clear_clean_block(chunk1.access.id)
    await file_transactions.local_storage.clear_clean_block(chunk2.access.id)

    data = await file_transactions.fd_read(fd, 14, 0)
    assert data == chunk1_data + chunk2_data[:4]


size = st.integers(min_value=0, max_value=4 * 1024 ** 2)  # Between 0 and 4MB


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="Windows file style not compatible with oracle")
def test_file_operations(
    tmpdir, hypothesis_settings, reset_testbed, file_transactions_factory, alice, alice_backend_cmds
):
    tentative = 0

    class FileOperationsStateMachine(TrioAsyncioRuleBasedStateMachine):
        async def start_transactions(self):
            async def _transactions_controlled_cb(started_cb):
                async with WorkspaceStorage.run(alice, Path("/dummy"), EntryID()) as local_storage:
                    file_transactions = await file_transactions_factory(
                        self.device, alice_backend_cmds, local_storage=local_storage
                    )
                    await started_cb(file_transactions=file_transactions)

            self.transactions_controller = await self.get_root_nursery().start(
                call_with_control, _transactions_controlled_cb
            )

        @initialize()
        async def init(self):
            nonlocal tentative
            tentative += 1
            await reset_testbed()

            self.device = alice
            await self.start_transactions()
            self.file_transactions = self.transactions_controller.file_transactions
            self.local_storage = self.file_transactions.local_storage

            self.fresh_manifest = LocalFileManifest.new_placeholder(parent=EntryID())
            self.entry_id = self.fresh_manifest.id
            async with self.local_storage.lock_entry_id(self.entry_id):
                await self.local_storage.set_manifest(self.entry_id, self.fresh_manifest)

            self.fd = self.local_storage.create_file_descriptor(self.fresh_manifest)
            self.file_oracle_path = tmpdir / f"oracle-test-{tentative}.txt"
            self.file_oracle_fd = os.open(self.file_oracle_path, os.O_RDWR | os.O_CREAT)

        async def teardown(self):
            if not hasattr(self, "fd"):
                return
            await self.file_transactions.fd_close(self.fd)
            os.close(self.file_oracle_fd)

        @rule(size=size, offset=size)
        async def read(self, size, offset):
            data = await self.file_transactions.fd_read(self.fd, size, offset)
            os.lseek(self.file_oracle_fd, offset, os.SEEK_SET)
            expected = os.read(self.file_oracle_fd, size)
            assert data == expected

        @rule(content=st.binary(), offset=size)
        async def write(self, content, offset):
            await self.file_transactions.fd_write(self.fd, content, offset)
            os.lseek(self.file_oracle_fd, offset, os.SEEK_SET)
            os.write(self.file_oracle_fd, content)

        @rule(length=size)
        async def resize(self, length):
            await self.file_transactions.fd_resize(self.fd, length)
            os.ftruncate(self.file_oracle_fd, length)

        @rule()
        async def reopen(self):
            await self.file_transactions.fd_close(self.fd)
            self.fd = self.local_storage.create_file_descriptor(self.fresh_manifest)
            os.close(self.file_oracle_fd)
            self.file_oracle_fd = os.open(self.file_oracle_path, os.O_RDWR)

    run_state_machine_as_test(FileOperationsStateMachine, settings=hypothesis_settings)
