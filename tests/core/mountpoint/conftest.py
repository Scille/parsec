# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import threading
from inspect import iscoroutinefunction
from pathlib import Path
from queue import Queue

import pytest
import trio

from parsec.core.mountpoint import mountpoint_manager_factory
from parsec.utils import start_task


@pytest.fixture
def base_mountpoint(tmpdir):
    return Path(tmpdir / "base_mountpoint")


@pytest.fixture
@pytest.mark.mountpoint
def mountpoint_service_factory(tmpdir, local_device_factory, user_fs_factory, reset_testbed):
    """
    Run a trio loop with fs and mountpoint manager in a separate thread to
    allow blocking operations on the mountpoint in the test
    """

    async def _run_mountpoint(
        base_mountpoint, bootstrap_cb, *, task_status=trio.TASK_STATUS_IGNORED
    ):
        device = local_device_factory()
        async with user_fs_factory(device) as user_fs:

            async with mountpoint_manager_factory(
                user_fs, user_fs.event_bus, base_mountpoint, debug=False
            ) as mountpoint_manager:

                if bootstrap_cb:
                    await bootstrap_cb(user_fs, mountpoint_manager)

                task_status.started((user_fs, mountpoint_manager))
                await trio.sleep_forever()

    async def _run_mountpoint_service_trio_loop(ready_queue):
        async with trio.open_service_nursery() as nursery:
            ready_queue.put((trio.hazmat.current_trio_token(), nursery))
            await trio.sleep_forever()

    class MountpointService:
        def __init__(self, base_mountpoint, trio_token, task):
            self.base_mountpoint = base_mountpoint
            self._trio_token = trio_token
            self._task = task

        @classmethod
        def build(cls, base_mountpoint, trio_token, nursery, bootstrap_cb=None):
            task = trio.from_thread.run(
                start_task,
                nursery,
                _run_mountpoint,
                base_mountpoint,
                bootstrap_cb,
                trio_token=trio_token,
            )
            return cls(base_mountpoint, trio_token, task)

        def execute(self, cb):
            if iscoroutinefunction(cb):
                trio.from_thread.run(cb, *self._task.value, trio_token=self._trio_token)
            else:
                trio.from_thread.run_sync(cb, *self._task.value, trio_token=self._trio_token)

        def stop(self):
            async def _do():
                await self._task.cancel_and_join()
                await reset_testbed()

            trio.from_thread.run(_do, trio_token=self._trio_token)

    # Run a trio loop in a thread
    ready_queue = Queue(1)
    thread = threading.Thread(
        target=trio.run, args=(_run_mountpoint_service_trio_loop, ready_queue)
    )
    thread.setName("MountpointServiceTrioLoop")
    thread.start()
    trio_token, nursery = ready_queue.get()

    count = 0

    def _mountpoint_service_factory(bootstrap_cb=None):
        nonlocal count
        count += 1
        return MountpointService.build(
            Path(tmpdir / f"mountpoint-svc-{count}"), trio_token, nursery, bootstrap_cb
        )

    try:
        yield _mountpoint_service_factory
    finally:
        trio.from_thread.run_sync(nursery.cancel_scope.cancel, trio_token=trio_token)
        thread.join()


@pytest.fixture
def mountpoint_service(mountpoint_service_factory):
    wid = None
    wpath = None

    async def _bootstrap(user_fs, mountpoint_manager):
        nonlocal wid, wpath
        wid = await user_fs.workspace_create("w")
        wpath = await mountpoint_manager.mount_workspace(wid)

    service = mountpoint_service_factory(_bootstrap)
    service.wid = wid
    service.wpath = wpath
    return service
