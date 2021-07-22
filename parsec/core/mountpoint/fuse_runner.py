# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import os
import sys
import trio
import errno
import ctypes
import signal
import threading
import importlib_resources
from pathlib import PurePath
from fuse import FUSE
from structlog import get_logger
from contextlib import contextmanager
from itertools import count

from parsec.event_bus import EventBus
from parsec.core import resources as resources_module
from parsec.core.fs.userfs import UserFS
from parsec.core.fs.workspacefs import WorkspaceFS
from parsec.core.core_events import CoreEvent
from parsec.core.mountpoint.fuse_operations import FuseOperations
from parsec.core.mountpoint.thread_fs_access import ThreadFSAccess
from parsec.core.mountpoint.exceptions import MountpointDriverCrash


__all__ = ("fuse_mountpoint_runner",)


logger = get_logger()


@contextmanager
def _reset_signals(signals=None):
    """A context that save the current signal handlers restore them when leaving.

    By default, it does so for SIGINT, SIGTERM, SIGHUP and SIGPIPE.
    """
    if signals is None:
        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE)
    saved = {sig: ctypes.pythonapi.PyOS_getsig(sig) for sig in signals}
    try:
        yield
    finally:
        for sig, handler in saved.items():
            if ctypes.pythonapi.PyOS_getsig(sig) != handler:
                ctypes.pythonapi.PyOS_setsig(sig, handler)


async def _bootstrap_mountpoint(base_mountpoint_path: PurePath, workspace_fs) -> PurePath:
    # Find a suitable path where to mount the workspace. The check we are doing
    # here are not atomic (and the mount operation is not itself atomic anyway),
    # hence there is still edgecases where the mount can crash due to concurrent
    # changes on the mountpoint path
    workspace_name = workspace_fs.get_workspace_name()
    for tentative in count(1):
        if tentative == 1:
            dirname = workspace_name
        else:
            dirname = f"{workspace_name} ({tentative})"
        mountpoint_path = base_mountpoint_path / dirname

        try:
            # On POSIX systems, mounting target must exists
            trio_mountpoint_path = trio.Path(mountpoint_path)
            await trio_mountpoint_path.mkdir(mode=0o700, exist_ok=True, parents=True)
            base_st_dev = (await trio.Path(base_mountpoint_path).stat()).st_dev
            initial_st_dev = (await trio_mountpoint_path.stat()).st_dev
            if initial_st_dev != base_st_dev:
                # mountpoint_path seems to already have a mountpoint on it,
                # hence find another place to setup our own mountpoint
                continue
            if list(await trio_mountpoint_path.iterdir()):
                # mountpoint_path not empty, cannot mount there
                continue
            return mountpoint_path, initial_st_dev

        except OSError:
            # In case of hard crash, it's possible the FUSE mountpoint is still
            # mounted (but points to nothing). In such case just mount in
            # another place
            continue


async def _teardown_mountpoint(mountpoint_path):
    try:
        await trio.Path(mountpoint_path).rmdir()
    except OSError:
        pass


async def fuse_mountpoint_runner(
    user_fs: UserFS,
    workspace_fs: WorkspaceFS,
    base_mountpoint_path: PurePath,
    config: dict,
    event_bus: EventBus,
    *,
    task_status=trio.TASK_STATUS_IGNORED,
):
    """
    Raises:
        MountpointDriverCrash
    """
    fuse_thread_started = threading.Event()
    fuse_thread_stopped = threading.Event()
    trio_token = trio.lowlevel.current_trio_token()
    fs_access = ThreadFSAccess(trio_token, workspace_fs)

    mountpoint_path, initial_st_dev = await _bootstrap_mountpoint(
        base_mountpoint_path, workspace_fs
    )

    # Prepare event information
    event_kwargs = {
        "mountpoint": mountpoint_path,
        "workspace_id": workspace_fs.workspace_id,
        "timestamp": getattr(workspace_fs, "timestamp", None),
    }

    fuse_operations = FuseOperations(event_bus, fs_access, **event_kwargs)

    try:
        teardown_cancel_scope = None
        event_bus.send(CoreEvent.MOUNTPOINT_STARTING, **event_kwargs)

        async with trio.open_service_nursery() as nursery:

            # Let fusepy decode the paths using the current file system encoding
            # Note that this does not prevent the user from using a certain encoding
            # in the context of the parsec app and another encoding in the context of
            # an application accessing the mountpoint. In this case, an encoding error
            # might be raised while fuspy tries to decode the path. If that happens,
            # fuspy will log the error and simply return EINVAL, which is acceptable.
            encoding = sys.getfilesystemencoding()

            def _run_fuse_thread():
                with importlib_resources.path(resources_module, "parsec.icns") as parsec_icns_path:

                    fuse_platform_options = {}
                    if sys.platform == "darwin":
                        fuse_platform_options = {
                            "local": True,
                            "volname": workspace_fs.get_workspace_name(),
                            "volicon": str(parsec_icns_path.absolute()),
                        }
                        # osxfuse-specific options :
                        # - local : allows mountpoint to show up correctly in finder (+ desktop)
                        # - volname : specify volume name (default is OSXFUSE [...])
                        # - volicon : specify volume icon (default is macOS drive icon)

                    else:
                        fuse_platform_options = {"auto_unmount": True}

                    logger.info("Starting fuse thread...", mountpoint=mountpoint_path)
                    try:
                        # Do not let fuse start if the runner is stopping
                        # It's important that `fuse_thread_started` is set before the check
                        # in order to avoid race conditions
                        fuse_thread_started.set()
                        if teardown_cancel_scope is not None:
                            return
                        FUSE(
                            fuse_operations,
                            str(mountpoint_path.absolute()),
                            foreground=True,
                            encoding=encoding,
                            **fuse_platform_options,
                            **config,
                        )

                    except Exception as exc:
                        try:
                            errcode = errno.errorcode[exc.args[0]]
                        except (KeyError, IndexError):
                            errcode = f"Unknown error code: {exc}"
                        raise MountpointDriverCrash(
                            f"Fuse has crashed on {mountpoint_path}: {errcode}"
                        ) from exc

                    finally:
                        fuse_thread_stopped.set()

            # The fusepy runner (FUSE) relies on the `fuse_main_real` function from libfuse
            # This function is high-level helper on top of the libfuse API that is intended
            # for simple application. As such, it sets some signal handlers to exit cleanly
            # after a SIGTINT, a SIGTERM or a SIGHUP. This is, however, not compatible with
            # our multi-instance multi-threaded application. A simple workaround here is to
            # restore the signals to their previous state once the fuse instance is started.
            with _reset_signals():
                nursery.start_soon(
                    lambda: trio.to_thread.run_sync(_run_fuse_thread, cancellable=True)
                )
                await _wait_for_fuse_ready(mountpoint_path, fuse_thread_started, initial_st_dev)

            event_bus.send(CoreEvent.MOUNTPOINT_STARTED, **event_kwargs)
            task_status.started(mountpoint_path)

    finally:
        with trio.CancelScope(shield=True) as teardown_cancel_scope:
            await _stop_fuse_thread(
                mountpoint_path, fuse_operations, fuse_thread_started, fuse_thread_stopped
            )
            event_bus.send(CoreEvent.MOUNTPOINT_STOPPED, **event_kwargs)
            await _teardown_mountpoint(mountpoint_path)


async def _wait_for_fuse_ready(mountpoint_path, fuse_thread_started, initial_st_dev):
    trio_mountpoint_path = trio.Path(mountpoint_path)
    # Polling until fuse is ready
    while True:
        # Tick for 10 ms
        await trio.sleep(0.01)
        # Stat the root of the workspace
        try:
            stat = await trio_mountpoint_path.stat()
        # The directory does not exist for some reason
        except FileNotFoundError:
            raise
        # Looks like a revoked workspace has been mounted
        except PermissionError:
            break
        # Might be another OSError like errno 113 (No route to host)
        except OSError:
            break
        # The device field differs, the mountpoint is up and running
        if stat.st_dev != initial_st_dev:
            break


async def _stop_fuse_thread(
    mountpoint_path, fuse_operations, fuse_thread_started, fuse_thread_stopped
):
    # Nothing to do if the fuse thread is not running
    if fuse_thread_stopped.is_set() or not fuse_thread_started.is_set():
        return
    logger.info("Stopping fuse thread...", mountpoint=mountpoint_path)

    # Fuse exit in macOS
    if sys.platform == "darwin":

        # The schedule_exit() solution doesn't work on macOS, instead freezes the application for
        # 120 seconds before a timeout occurs. The solution used is to call this function (macOS
        # equivalent to fusermount) in a subprocess to unmount.
        process_args = ["diskutil", "unmount", "force", str(mountpoint_path)]
        process = await trio.open_process(process_args)

        # Perform 300 attempts of 10 ms, i.e a 3 second timeout
        # A while loop wouldn't be ideal here, especially since this code is protected against cancellation
        for _ in range(300):
            # Check if the attempt succeeded for 10 ms
            if await trio.to_thread.run_sync(fuse_thread_stopped.wait, 0.01):
                break
            # Restart the unmount process if necessary
            if process.poll() is not None:
                process = await trio.open_process(process_args)

        # Wait for unmount process to complete if necessary
        await process.wait()

    # Fuse exit in linux
    else:

        # Schedule an exit in the fuse operations
        fuse_operations.schedule_exit()

        # Ping fuse by performing an access from a daemon thread
        def _ping_fuse_thread_target():
            try:
                os.stat(mountpoint_path / ".__shutdown_fuse__")
            except OSError:
                pass

        # All kind of stuff can go wrong here if the access is performed while the fuse FS is already shutting down,
        # from exotic exceptions to blocking forever. For this reason, we don't use `await trio.Path(...).exists()`
        # but simply fire and forget a ping to the fuse FS. Either this ping succeeds in waking the FS up and thus
        # starting its shutdown procedure, or it fails because the FS is already shutting down.
        thread = threading.Thread(target=_ping_fuse_thread_target, daemon=True)
        thread.start()

        # Perform 100 attempts of 10 ms, i.e a 1 second timeout
        # A while loop wouldn't be ideal here, especially since this code is protected against cancellation
        for _ in range(100):
            # Check if the attempt succeeded for 10 ms
            if await trio.to_thread.run_sync(fuse_thread_stopped.wait, 0.01):
                break
            # Restart the daemon thread if necessary
            if not thread.is_alive():
                thread = threading.Thread(target=_ping_fuse_thread_target, daemon=True)
                thread.start()

    # The thread has now stopped
    logger.info("Fuse thread stopped", mountpoint=mountpoint_path)
