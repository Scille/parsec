# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import logging
import os
from importlib import __import__ as import_function
from pathlib import PurePath
from typing import Optional, Sequence

import trio
from async_generator import asynccontextmanager
from pendulum import Pendulum
from structlog import get_logger

from parsec.core.core_events import CoreEvent
from parsec.core.fs.exceptions import FSWorkspaceNotFoundError, FSWorkspaceTimestampedTooEarly
from parsec.core.fs.workspacefs import WorkspaceFSTimestamped
from parsec.core.mountpoint.exceptions import (
    MountpointAlreadyMounted,
    MountpointConfigurationError,
    MountpointConfigurationWorkspaceFSTimestampedError,
    MountpointError,
    MountpointFuseNotAvailable,
    MountpointNotMounted,
    MountpointWinfspNotAvailable,
)
from parsec.core.mountpoint.winify import winify_entry_name
from parsec.core.types import EntryID, FsPath
from parsec.core.win_registry import cleanup_parsec_drive_icons
from parsec.utils import start_task

# Importing winfspy can take some time (about 0.4 seconds)
# Let's import those bindings at module level, in order to
# avoid spending too much time importing them later while the
# trio loop is running.
try:
    if os.name == "nt":
        import_function("winfspy")
    else:
        import_function("fuse")
except (ImportError, RuntimeError):
    pass

logger = get_logger()


def get_mountpoint_runner():
    # Windows
    if os.name == "nt":

        try:
            # Use import function for easier mock up
            import_function("winfspy")
        except RuntimeError as exc:
            raise MountpointWinfspNotAvailable(exc) from exc

        logging.getLogger("winfspy").setLevel(logging.WARNING)
        from parsec.core.mountpoint.winfsp_runner import winfsp_mountpoint_runner

        return winfsp_mountpoint_runner

    # Linux
    else:
        try:
            # Use import function for easier mock up
            import_function("fuse")
        except ImportError as exc:
            raise MountpointFuseNotAvailable(exc) from exc

        logging.getLogger("fuse").setLevel(logging.WARNING)
        from parsec.core.mountpoint.fuse_runner import fuse_mountpoint_runner

        return fuse_mountpoint_runner


class MountpointManager:
    def __init__(self, user_fs, event_bus, base_mountpoint_path, config, runner, nursery):
        self.user_fs = user_fs
        self.event_bus = event_bus
        self.base_mountpoint_path = base_mountpoint_path
        self.config = config
        self._runner = runner
        self._nursery = nursery
        self._mountpoint_tasks = {}
        self._timestamped_workspacefs = {}

        if os.name == "nt":
            self._build_mountpoint_path = lambda base_path, parts: base_path / "\\".join(
                winify_entry_name(x) for x in parts
            )
        else:
            self._build_mountpoint_path = lambda base_path, parts: base_path / "/".join(parts)

    def _get_workspace(self, workspace_id: EntryID):
        try:
            return self.user_fs.get_workspace(workspace_id)
        except FSWorkspaceNotFoundError as exc:
            raise MountpointConfigurationError(f"Workspace `{workspace_id}` doesn't exist") from exc

    def _get_workspace_timestamped(self, workspace_id: EntryID, timestamp: Pendulum):
        try:
            return self._timestamped_workspacefs[workspace_id][timestamp]
        except KeyError:
            try:
                self.user_fs.get_workspace(workspace_id)
                raise MountpointNotMounted(
                    f"Workspace `{workspace_id}` not mounted at timestamped `{timestamp}`"
                )
            except FSWorkspaceNotFoundError as exc:
                raise MountpointConfigurationError(
                    f"Workspace `{workspace_id}` doesn't exist"
                ) from exc

    async def _load_workspace_timestamped(
        self, workspace_id: EntryID, timestamp: Pendulum
    ) -> WorkspaceFSTimestamped:
        try:
            return self._timestamped_workspacefs[workspace_id][timestamp]
        except KeyError:
            pass
        try:
            # Get a random WorkspaceFSTimestamped if possible, as all WorkspaceFSTimestamped from
            # the same WorkspaceFS will share the same cache when implemented
            source_workspace = next(v for v in self._timestamped_workspacefs[workspace_id].values())
        except (StopIteration, KeyError):  # No WorkspaceFSTimestamped found for this workspace_id
            source_workspace = self._get_workspace(workspace_id)
        try:
            new_workspace = await source_workspace.to_timestamped(timestamp)
        except FSWorkspaceTimestampedTooEarly as exc:
            raise MountpointConfigurationWorkspaceFSTimestampedError(
                f"Workspace `{workspace_id}` didn't exist at `{timestamp}`",
                workspace_id,
                timestamp,
                source_workspace.get_workspace_name(),
            ) from exc
        try:
            self._timestamped_workspacefs[workspace_id][timestamp] = new_workspace
        except KeyError:
            self._timestamped_workspacefs[workspace_id] = {timestamp: new_workspace}
        return new_workspace

    async def _mount_workspace_helper(self, workspace_fs, timestamp: Pendulum = None):
        key = (workspace_fs.workspace_id, timestamp)

        async def curried_runner(task_status=trio.TASK_STATUS_IGNORED):
            try:
                return await self._runner(
                    self.user_fs,
                    workspace_fs,
                    self.base_mountpoint_path,
                    config=self.config,
                    event_bus=self.event_bus,
                    task_status=task_status,
                )
            finally:
                self._mountpoint_tasks.pop(key, None)

        runner_task = await start_task(self._nursery, curried_runner)
        self._mountpoint_tasks[key] = runner_task
        return runner_task

    def get_path_in_mountpoint(
        self, workspace_id: EntryID, path: FsPath, timestamp: Pendulum = None
    ) -> PurePath:
        if timestamp is None:
            self._get_workspace(workspace_id)
        else:
            self._get_workspace_timestamped(workspace_id, timestamp)
        try:
            runner_task = self._mountpoint_tasks[(workspace_id, timestamp)]
            return self._build_mountpoint_path(runner_task.value, path.parts)

        except KeyError:
            raise MountpointNotMounted(f"Workspace `{workspace_id}` is not mounted")

    async def mount_workspace(self, workspace_id: EntryID, timestamp: Pendulum = None) -> PurePath:
        if (workspace_id, timestamp) in self._mountpoint_tasks:
            raise MountpointAlreadyMounted(f"Workspace `{workspace_id}` already mounted.")

        if timestamp is not None:
            return await self.remount_workspace_new_timestamp(workspace_id, None, timestamp)

        workspace = self._get_workspace(workspace_id)
        runner_task = await self._mount_workspace_helper(workspace)
        return runner_task.value

    async def unmount_workspace(self, workspace_id: EntryID, timestamp: Pendulum = None):
        if (workspace_id, timestamp) not in self._mountpoint_tasks:
            raise MountpointNotMounted(f"Workspace `{workspace_id}` not mounted.")

        await self._mountpoint_tasks[(workspace_id, timestamp)].cancel_and_join()

    async def remount_workspace_new_timestamp(
        self, workspace_id: EntryID, original_timestamp: Pendulum, target_timestamp: Pendulum
    ) -> PurePath:
        """
        Mount the workspace at target_timestamp, and unmount the workspace at the original
        timestamp if it is not None. If both timestamps are equals, do nothing.
        """
        # TODO : use different workspaces for temp mount
        if original_timestamp == target_timestamp:
            try:
                return self._mountpoint_tasks[(workspace_id, target_timestamp)].value
            except KeyError:
                pass
        new_workspace = await self._load_workspace_timestamped(workspace_id, target_timestamp)

        runner_task = await self._mount_workspace_helper(new_workspace, target_timestamp)
        if original_timestamp is not None:
            if (workspace_id, original_timestamp) not in self._mountpoint_tasks:
                raise MountpointNotMounted(f"Workspace `{workspace_id}` not mounted.")

            await self._mountpoint_tasks[(workspace_id, original_timestamp)].cancel_and_join()
        return runner_task.value

    async def get_timestamped_mounted(self):
        return {
            workspace_id_and_ts: self._get_workspace_timestamped(*workspace_id_and_ts)
            for workspace_id_and_ts in self._mountpoint_tasks.keys()
            if workspace_id_and_ts[1] is not None
        }

    def is_workspace_mounted(self, workspace_id: EntryID, timestamp=None) -> bool:
        return (workspace_id, timestamp) in self._mountpoint_tasks

    async def safe_mount(self, workspace_id: EntryID) -> None:
        try:
            await self.mount_workspace(workspace_id)

        # The workspace could not be mounted
        # Maybe be a `MountpointAlreadyMounted` or `MountpointNoDriveAvailable`
        except MountpointError:
            pass

        # Unexpected exception is not a reason to crash the mountpoint manager
        except Exception:
            logger.exception("Unexpected error while mounting a new workspace")

    async def safe_unmount(
        self, workspace_id: EntryID, timestamp: Optional[Pendulum] = None
    ) -> None:
        try:
            await self.unmount_workspace(workspace_id, timestamp)

        # The workspace could not be mounted
        # Maybe be a `MountpointAlreadyMounted` or `MountpointNoDriveAvailable`
        except MountpointError:
            pass

        # Unexpected exception is not a reason to crash the mountpoint manager
        except Exception:
            logger.exception("Unexpected error while unmounting a revoked workspace")

    async def safe_mount_all(self, exclude: Sequence[EntryID] = ()):
        exclude_set = set(exclude)
        user_manifest = self.user_fs.get_user_manifest()
        for workspace_entry in user_manifest.workspaces:
            if workspace_entry.role is None or workspace_entry.id in exclude_set:
                continue
            await self.safe_mount(workspace_entry.id)

    async def safe_unmount_all(self):
        for workspace_id, timestamp in list(self._mountpoint_tasks.keys()):
            await self.safe_unmount(workspace_id, timestamp=timestamp)


@asynccontextmanager
async def mountpoint_manager_factory(
    user_fs,
    event_bus,
    base_mountpoint_path,
    *,
    debug: bool = False,
    mount_all: bool = False,
    mount_on_workspace_created: bool = False,
    mount_on_workspace_shared: bool = False,
    unmount_on_workspace_revoked: bool = False,
    exclude_from_mount_all: list = (),
):
    config = {"debug": debug}

    runner = get_mountpoint_runner()

    # Now is a good time to perform some cleanup in the registry
    if os.name == "nt":
        cleanup_parsec_drive_icons()

    def on_event(event, new_entry, previous_entry=None):
        # Workspace created
        if event == CoreEvent.FS_WORKSPACE_CREATED:
            if mount_on_workspace_created:
                mount_nursery.start_soon(mountpoint_manager.safe_mount, new_entry.id)
            return

        # Workspace revoked
        if event == CoreEvent.SHARING_UPDATED and new_entry.role is None:
            if unmount_on_workspace_revoked:
                mount_nursery.start_soon(mountpoint_manager.safe_unmount, new_entry.id)
            return

        # Workspace shared
        if event == CoreEvent.SHARING_UPDATED and previous_entry is None:
            if mount_on_workspace_shared:
                mount_nursery.start_soon(mountpoint_manager.safe_mount, new_entry.id)
            return

    # Instantiate the mountpoint manager with its own nursery
    async with trio.open_service_nursery() as nursery:
        mountpoint_manager = MountpointManager(
            user_fs, event_bus, base_mountpoint_path, config, runner, nursery
        )

        # Exit this context by unmounting all mountpoints
        try:

            # A nursery dedicated to new workspace events
            async with trio.open_service_nursery() as mount_nursery:

                # Setup new workspace events
                with event_bus.connect_in_context(
                    (CoreEvent.FS_WORKSPACE_CREATED, on_event),
                    (CoreEvent.SHARING_UPDATED, on_event),
                ):

                    # Mount required workspaces
                    if mount_all:
                        await mountpoint_manager.safe_mount_all(exclude=exclude_from_mount_all)

                    # Yield point
                    yield mountpoint_manager

                    # Cancel current mount_workspace tasks
                    mount_nursery.cancel_scope.cancel()

        # Unmount all the workspaces (should this be shielded?)
        finally:
            await mountpoint_manager.safe_unmount_all()

        # Cancel the mountpoint tasks (although they should all be finised by now)
        nursery.cancel_scope.cancel()
