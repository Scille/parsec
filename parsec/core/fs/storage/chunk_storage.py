# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import time

import trio
from async_generator import asynccontextmanager

from parsec.core.fs.exceptions import FSLocalMissError
from parsec.core.fs.storage.local_database import LocalDatabase
from parsec.core.types import DEFAULT_BLOCK_SIZE, ChunkID, LocalDevice


class ChunkStorage:
    """Interface to access the local chunks of data."""

    def __init__(self, device: LocalDevice, localdb: LocalDatabase):
        self.local_symkey = device.local_symkey
        self.localdb = localdb

    @property
    def path(self):
        return self.localdb.path

    @classmethod
    @asynccontextmanager
    async def run(cls, *args, **kwargs):
        self = cls(*args, **kwargs)
        await self._create_db()
        try:
            yield self
        finally:
            with trio.CancelScope(shield=True):
                await self.localdb.commit()

    def _open_cursor(self):
        # There is no point in commiting dirty chunks:
        # they are referenced by a manifest that will get commited
        # soon after them. This greatly improves the performance of
        # writing file using the mountpoint as the OS will typically
        # writes data as blocks of 4K. The manifest being kept in
        # memory during the writing, this means that the data and
        # metadata is typically not flushed to the disk until an
        # an acutal flush operation is performed.
        return self.localdb.open_cursor(commit=False)

    # Database initialization

    async def _create_db(self):
        async with self._open_cursor() as cursor:
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS chunks
                    (chunk_id BLOB PRIMARY KEY NOT NULL, -- UUID
                     size INTEGER NOT NULL,
                     offline INTEGER NOT NULL,  -- Boolean
                     accessed_on REAL, -- Timestamp
                     data BLOB NOT NULL
                );"""
            )

    # Size and chunks

    async def get_nb_blocks(self):
        async with self._open_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM chunks")
            result, = cursor.fetchone()
            return result

    async def get_total_size(self):
        async with self._open_cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(size), 0) FROM chunks")
            result, = cursor.fetchone()
            return result

    # Generic chunk operations

    async def is_chunk(self, chunk_id: ChunkID):
        async with self._open_cursor() as cursor:
            cursor.execute("SELECT chunk_id FROM chunks WHERE chunk_id = ?", (chunk_id.bytes,))
            manifest_row = cursor.fetchone()
        return bool(manifest_row)

    async def get_chunk(self, chunk_id: ChunkID):
        async with self._open_cursor() as cursor:
            cursor.execute(
                """
                UPDATE chunks SET accessed_on = ? WHERE chunk_id = ?;
                """,
                (time.time(), chunk_id.bytes),
            )
            cursor.execute("SELECT changes()")
            changes, = cursor.fetchone()
            if not changes:
                raise FSLocalMissError(chunk_id)

            cursor.execute("""SELECT data FROM chunks WHERE chunk_id = ?""", (chunk_id.bytes,))
            ciphered, = cursor.fetchone()

        return self.local_symkey.decrypt(ciphered)

    async def set_chunk(self, chunk_id: ChunkID, raw: bytes):
        assert isinstance(raw, (bytes, bytearray))
        ciphered = self.local_symkey.encrypt(raw)

        # Update database
        async with self._open_cursor() as cursor:
            cursor.execute(
                """INSERT OR REPLACE INTO
                chunks (chunk_id, size, offline, accessed_on, data)
                VALUES (?, ?, ?, ?, ?)""",
                (chunk_id.bytes, len(ciphered), False, time.time(), ciphered),
            )

    async def clear_chunk(self, chunk_id: ChunkID):
        async with self._open_cursor() as cursor:
            cursor.execute("DELETE FROM chunks WHERE chunk_id = ?", (chunk_id.bytes,))
            cursor.execute("SELECT changes()")
            changes, = cursor.fetchone()

        if not changes:
            raise FSLocalMissError(chunk_id)


class BlockStorage(ChunkStorage):
    """Interface for caching the data blocks."""

    def __init__(self, device: LocalDevice, localdb: LocalDatabase, cache_size: int):
        super().__init__(device, localdb)
        self.cache_size = cache_size

    def _open_cursor(self):
        # It doesn't matter for blocks to be commited as soon as they're added
        # since they exists in the remote storage anyway. But it's simply more
        # convenient to perform the commit right away as does't cost much (at
        # least compare to the downloading of the block).
        return self.localdb.open_cursor(commit=True)

    # Garbage collection

    @property
    def block_limit(self):
        return self.cache_size // DEFAULT_BLOCK_SIZE

    async def clear_all_blocks(self):
        async with self._open_cursor() as cursor:
            cursor.execute("DELETE FROM chunks")

    async def clear_old_blocks(self, limit):
        async with self._open_cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM chunks WHERE chunk_id IN (
                    SELECT chunk_id FROM chunks ORDER BY accessed_on ASC LIMIT ?
                )
                """,
                (limit,),
            )

    # Upgraded set method

    async def set_chunk(self, chunk_id: ChunkID, raw: bytes):
        # Actual set operation
        await super().set_chunk(chunk_id, raw)

        # Clean up if necessary
        nb_blocks = await self.get_nb_blocks()
        extra_blocks = nb_blocks - self.block_limit
        if extra_blocks > 0:

            # Remove the extra block plus 10 % of the cache size, i.e about 100 blocks
            limit = extra_blocks + self.block_limit // 10
            await self.clear_old_blocks(limit=limit)
