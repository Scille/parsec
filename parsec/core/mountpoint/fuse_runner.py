# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import ctypes
import errno
import signal
import sys
import threading
from contextlib import contextmanager
from itertools import count
from pathlib import PurePath

import trio
from fuse import FUSE
from structlog import get_logger

from parsec.core.core_events import CoreEvent
from parsec.core.mountpoint.exceptions import MountpointDriverCrash
from parsec.core.mountpoint.fuse_operations import FuseOperations
from parsec.core.mountpoint.thread_fs_access import ThreadFSAccess

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
        with trio.CancelScope(shield=True):
            await trio.Path(mountpoint_path).rmdir()
    except OSError:
        pass


async def fuse_mountpoint_runner(
    user_fs,
    workspace_fs,
    base_mountpoint_path: PurePath,
    config: dict,
    event_bus,
    *,
    task_status=trio.TASK_STATUS_IGNORED,
):
    """
    Raises:
        MountpointDriverCrash
    """
    fuse_thread_started = threading.Event()
    fuse_thread_stopped = threading.Event()
    trio_token = trio.hazmat.current_trio_token()
    fs_access = ThreadFSAccess(trio_token, workspace_fs)
    fuse_operations = FuseOperations(event_bus, fs_access)

    mountpoint_path, initial_st_dev = await _bootstrap_mountpoint(
        base_mountpoint_path, workspace_fs
    )

    # Prepare event information
    event_kwargs = {
        "mountpoint": mountpoint_path,
        "workspace_id": workspace_fs.workspace_id,
        "timestamp": getattr(workspace_fs, "timestamp", None),
    }
    try:
        event_bus.send(CoreEvent.MOUNTPOINT_STARTING, **event_kwargs)

        async with trio.open_service_nursery() as nursery:

            # Let fusepy decode the paths using the current file system encoding
            # Note that this does not prevent the user from using a certain encoding
            # in the context of the parsec app and another encoding in the context of
            # an application accessing the mountpoint. In this case, an encoding error
            # might be raised while fuspy tries to decode the path. If that happends,
            # fuspy will log the error and simply return EINVAL, which is acceptable.
            encoding = sys.getfilesystemencoding()

            def _run_fuse_thread():
                logger.info("Starting fuse thread...", mountpoint=mountpoint_path)
                try:
                    fuse_thread_started.set()
                    FUSE(
                        fuse_operations,
                        str(mountpoint_path.absolute()),
                        foreground=True,
                        auto_unmount=True,
                        encoding=encoding,
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
        await _stop_fuse_thread(mountpoint_path, fuse_operations, fuse_thread_stopped)
        event_bus.send(CoreEvent.MOUNTPOINT_STOPPED, **event_kwargs)
        await _teardown_mountpoint(mountpoint_path)


async def _wait_for_fuse_ready(mountpoint_path, fuse_thread_started, initial_st_dev):
    trio_mountpoint_path = trio.Path(mountpoint_path)

    # Polling until fuse is ready
    while True:
        await trio.sleep(0.01)
        try:
            stat = await trio_mountpoint_path.stat()
        except FileNotFoundError:
            continue
        # Looks like a revoked workspace has been mounted
        except PermissionError:
            break
        # Might be another OSError like errno 113 (No route to host)
        except OSError:
            break
        if stat.st_dev != initial_st_dev:
            break


async def _stop_fuse_thread(mountpoint_path, fuse_operations, fuse_thread_stopped):
    if fuse_thread_stopped.is_set():
        return

    # Ask for dummy file just to force a fuse operation that will
    # process the `fuse_exit` from a valid context

    with trio.CancelScope(shield=True):
        logger.info("Stopping fuse thread...", mountpoint=mountpoint_path)
        fuse_operations.schedule_exit()
        try:
            await trio.Path(mountpoint_path / "__shutdown_fuse__").exists()
        except OSError:
            pass
        await trio.to_thread.run_sync(fuse_thread_stopped.wait)
        logger.info("Fuse thread stopped", mountpoint=mountpoint_path)
