# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import errno
import os
from contextlib import contextmanager
from stat import S_IFDIR, S_IFREG, S_IRWXU
from typing import Optional

from fuse import FuseOSError, LoggingMixIn, Operations, fuse_exit, fuse_get_context
from structlog import get_logger

from parsec.core.core_events import CoreEvent
from parsec.core.fs import FSLocalOperationError, FSRemoteOperationError
from parsec.core.types import FsPath

logger = get_logger()
MODES = {os.O_RDONLY: "r", os.O_WRONLY: "w", os.O_RDWR: "rw"}


# We are preventing the creation of file and folders starting with those prefixes
# It might not be the best solution but it does fix our problems for the moment.
# In particular:
#
# - .Trash-XXXX which is used to store removed files and directories
#   We don't really want this to be shared among users and our system already provides
#   backup capabilities.
BANNED_PREFIXES = (".Trash-",)


def is_banned(name):
    return any(name.startswith(prefix) for prefix in BANNED_PREFIXES)


@contextmanager
def translate_error(event_bus, operation, path):
    try:
        yield

    except FuseOSError:
        raise

    except FSLocalOperationError as exc:
        raise FuseOSError(exc.errno) from exc

    except FSRemoteOperationError as exc:
        event_bus.send(CoreEvent.MOUNTPOINT_REMOTE_ERROR, exc=exc, operation=operation, path=path)
        raise FuseOSError(exc.errno) from exc

    except Exception as exc:
        logger.exception("Unhandled exception in fuse mountpoint")
        event_bus.send(
            CoreEvent.MOUNTPOINT_UNHANDLED_ERROR, exc=exc, operation=operation, path=path
        )
        # Use EINVAL as fallback error code, since this is what fusepy does.
        raise FuseOSError(errno.EINVAL) from exc


class FuseOperations(LoggingMixIn, Operations):
    def __init__(self, event_bus, fs_access):
        super().__init__()
        self.event_bus = event_bus
        self.fs_access = fs_access
        self.fds = {}
        self._need_exit = False

    def __call__(self, name, path, *args, **kwargs):
        # The path argument might be None or "-" in some special cases
        # related to `release` and `releasedir` (when the file descriptor
        # is available but the corresponding path is not). In those cases,
        # we can simply ignore the path.
        path = FsPath(path) if path not in (None, "-") else None
        with translate_error(self.event_bus, name, path):
            return super().__call__(name, path, *args, **kwargs)

    def schedule_exit(self):
        # TODO: Currently call fuse_exit from a non fuse thread is not possible
        # (see https://github.com/fusepy/fusepy/issues/116).
        self._need_exit = True

    def init(self, path: FsPath):
        pass

    def getattr(self, path: FsPath, fh: Optional[int] = None):
        if self._need_exit:
            fuse_exit()

        stat = self.fs_access.entry_info(path)

        fuse_stat = {}
        # Set it to 777 access
        fuse_stat["st_mode"] = 0
        if stat["type"] == "folder":
            fuse_stat["st_mode"] |= S_IFDIR
            fuse_stat["st_size"] = 4096  # Because why not ?
            fuse_stat["st_nlink"] = 2
        else:
            fuse_stat["st_mode"] |= S_IFREG
            fuse_stat["st_size"] = stat["size"]
            fuse_stat["st_nlink"] = 1
        fuse_stat["st_blocks"] = fuse_stat["st_size"] // 512
        if fuse_stat["st_size"] % 512:
            fuse_stat["st_blocks"] += 1
        fuse_stat["st_mode"] |= S_IRWXU
        fuse_stat["st_ctime"] = stat["created"].timestamp()  # TODO change to local timezone
        fuse_stat["st_mtime"] = stat["updated"].timestamp()
        fuse_stat["st_atime"] = stat["updated"].timestamp()  # TODO not supported ?
        uid, gid, _ = fuse_get_context()
        fuse_stat["st_uid"] = uid
        fuse_stat["st_gid"] = gid
        return fuse_stat

    def readdir(self, path: FsPath, fh: int):
        stat = self.fs_access.entry_info(path)

        if stat["type"] == "file":
            raise FuseOSError(errno.ENOTDIR)

        return [".", ".."] + list(stat["children"])

    def create(self, path: FsPath, mode: int):
        if is_banned(path.name):
            raise FuseOSError(errno.EACCES)
        _, fd = self.fs_access.file_create(path, open=True)
        return fd

    def open(self, path: FsPath, flags: int = 0):
        # Filter file status and file creation flags
        mode = MODES[flags % 4]
        _, fd = self.fs_access.file_open(path, mode=mode)
        return fd

    def release(self, path: FsPath, fh: int):
        self.fs_access.fd_close(fh)

    def read(self, path: FsPath, size: int, offset: int, fh: int):
        # Atomic read
        ret = self.fs_access.fd_read(fh, size, offset, raise_eof=False)
        # Fuse wants bytes but fd_read returns a bytearray
        return bytes(ret)

    def write(self, path: FsPath, data: bytes, offset: int, fh: int):
        return self.fs_access.fd_write(fh, data, offset)

    def truncate(self, path: FsPath, length: int, fh: Optional[int] = None):
        if fh:
            self.fs_access.fd_resize(fh, length)
        else:
            self.fs_access.file_resize(path, length)

    def unlink(self, path: FsPath):
        self.fs_access.file_delete(path)

    def mkdir(self, path: FsPath, mode: int):
        if is_banned(path.name):
            raise FuseOSError(errno.EACCES)
        self.fs_access.folder_create(path)
        return 0

    def rmdir(self, path: FsPath):
        self.fs_access.folder_delete(path)
        return 0

    def rename(self, path: FsPath, destination: str):
        destination = FsPath(destination)
        self.fs_access.entry_rename(path, destination, overwrite=True)
        return 0

    def flush(self, path: FsPath, fh: int):
        self.fs_access.fd_flush(fh)
        return 0

    def fsync(self, path: FsPath, datasync, fh: int):
        self.fs_access.fd_flush(fh)
        return 0

    def fsyncdir(self, path: FsPath, datasync, fh: int):
        return 0  # TODO
