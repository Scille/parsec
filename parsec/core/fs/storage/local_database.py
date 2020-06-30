# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import functools
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlite3 import connect as sqlite_connect

import outcome
import trio
from async_generator import asynccontextmanager


@asynccontextmanager
async def thread_pool_runner(max_workers=None):
    """A trio-managed thread pool.

    This should be removed if trio decides to add support for thread pools:
    https://github.com/python-trio/trio/blob/c5497c5ac4/trio/_threads.py#L32-L128
    """
    executor = ThreadPoolExecutor(max_workers=max_workers)
    trio_token = trio.hazmat.current_trio_token()

    async def run_in_thread(fn, *args):
        send_channel, receive_channel = trio.open_memory_channel(1)

        def target():
            result = outcome.capture(fn, *args)
            trio.from_thread.run_sync(send_channel.send_nowait, result, trio_token=trio_token)

        executor.submit(target)
        result = await receive_channel.receive()
        return result.unwrap()

    # The thread pool executor cannot be used as a sync context here, as it would
    # block the trio loop. Instead, we shut the executor down in a worker thread.
    try:
        yield run_in_thread
    finally:
        with trio.CancelScope(shield=True):
            await trio.to_thread.run_sync(executor.shutdown)


def protect_with_lock(fn):
    """Use as a decorator to protect an async method with `self._lock`.

    Also works with async gen method so it can be used for `open_cursor`.
    """

    if inspect.isasyncgenfunction(fn):

        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            async with self._lock:
                async for item in fn.__get__(self)(*args, **kwargs):
                    yield item

    else:
        assert inspect.iscoroutinefunction(fn)

        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            async with self._lock:
                return await fn.__get__(self)(*args, **kwargs)

    return wrapper


class LocalDatabase:
    """Base class for managing an sqlite3 connection."""

    def __init__(self, path, vacuum_threshold=None):
        self._conn = None
        self._lock = trio.Lock()
        self._run_in_thread = None

        self.path = Path(path)
        self.vacuum_threshold = vacuum_threshold

    @classmethod
    @asynccontextmanager
    async def run(cls, *args, **kwargs):
        # Instanciate the local database
        self = cls(*args, **kwargs)

        # Run a pool with single worker thread
        # (although the lock already protects against concurrent access to the pool)
        async with thread_pool_runner(max_workers=1) as self._run_in_thread:

            # Create the connection to the sqlite database
            try:
                await self._connect()

                # Yield the instance
                yield self

            # Safely flush and close the connection
            finally:
                with trio.CancelScope(shield=True):
                    await self._close()

    # Life cycle

    async def _create_connection(self):
        # Create directories
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Create sqlite connection
        conn = sqlite_connect(str(self.path), check_same_thread=False)

        # The default isolation level ("") lets python manage the transaction
        # so we can periodically commit the pending changes.
        assert conn.isolation_level == ""

        # The combination of WAL journal mode and NORMAL synchronous mode
        # is a great combination: it allows for fast commits (~10 us compare
        # to 15 ms the default mode) but still protects the database against
        # corruption in the case of OS crash or power failure.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        # Return connection
        return conn

    @protect_with_lock
    async def _connect(self):
        if self._conn is not None:
            raise RuntimeError("Already connected")

        # Connect and initialize database
        self._conn = await self._create_connection()

    @protect_with_lock
    async def _close(self):
        # Idempotency
        if self._conn is None:
            return

        # Commit and close
        await self._run_in_thread(self._conn.commit)
        self._conn.close()
        self._conn = None

    # Cursor management

    @asynccontextmanager
    @protect_with_lock
    async def open_cursor(self, commit=True):
        # Get a cursor
        cursor = self._conn.cursor()
        try:

            # Execute SQL commands
            yield cursor

            # Commit the transaction when finished
            if commit and self._conn.in_transaction:
                await self._run_in_thread(self._conn.commit)

        # Close cursor
        finally:
            cursor.close()

    @protect_with_lock
    async def commit(self):
        await self._run_in_thread(self._conn.commit)

    # Vacuum

    def get_disk_usage(self):
        disk_usage = 0
        for suffix in (".sqlite", ".sqlite-wal", ".sqlite-shm"):
            try:
                disk_usage += self.path.with_suffix(suffix).stat().st_size
            except OSError:
                pass
        return disk_usage

    @protect_with_lock
    async def run_vacuum(self):
        # Vacuum disabled
        if self.vacuum_threshold is None:
            return

        # Flush to disk
        await self._run_in_thread(self._conn.commit)

        # No reason to vacuum yet
        if self.get_disk_usage() < self.vacuum_threshold:
            return

        # Run vacuum
        await self._run_in_thread(self._conn.execute, "VACUUM")

        # The connection needs to be recreated
        try:
            self._conn.close()
        finally:
            self._conn = await self._create_connection()
