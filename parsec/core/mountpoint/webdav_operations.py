# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import errno
from functools import wraps
from contextlib import contextmanager
from structlog import get_logger
from wsgidav.dav_error import (
    DAVError,
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_CONFLICT,
    HTTP_FAILED_DEPENDENCY,
    HTTP_INTERNAL_ERROR,
)
from wsgidav.util import guess_mime_type
from wsgidav.dav_provider import DAVNonCollection, DAVCollection, DAVProvider

from parsec.core.types import FsPath
from parsec.core.fs import FSLocalOperationError, FSRemoteOperationError, FSFileNotFoundError


logger = get_logger()


ERRNO_TO_HTTP = {
    errno.EINVAL: HTTP_BAD_REQUEST,
    errno.EACCES: HTTP_FORBIDDEN,
    errno.EROFS: HTTP_FORBIDDEN,
    errno.ENOTDIR: HTTP_NOT_FOUND,
    errno.ENOENT: HTTP_NOT_FOUND,
    errno.EXDEV: HTTP_BAD_REQUEST,
    errno.EEXIST: HTTP_CONFLICT,
    errno.EISDIR: HTTP_BAD_REQUEST,
    errno.ENOTEMPTY: HTTP_BAD_REQUEST,
    errno.EBADF: HTTP_BAD_REQUEST,
    errno.EHOSTUNREACH: HTTP_FAILED_DEPENDENCY,
}


def errno_to_http(errno):
    return ERRNO_TO_HTTP.get(errno, HTTP_BAD_REQUEST)


@contextmanager
def translate_error(event_bus, operation: str, path: FsPath):
    try:
        yield

    except DAVError:
        raise

    except FSLocalOperationError as exc:
        print("FSLocalOperationError:", repr(exc))
        raise DAVError(errno_to_http(exc.errno)) from exc

    except FSRemoteOperationError as exc:
        print("FSRemoteOperationError:", repr(exc))
        event_bus.send("mountpoint.remote_error", exc=exc, operation=operation, path=path)
        raise DAVError(errno_to_http(exc.errno)) from exc

    except Exception as exc:
        print("Exception:", repr(exc))
        logger.exception("Unhandled exception in fuse mountpoint")
        event_bus.send("mountpoint.unhandled_error", exc=exc, operation=operation, path=path)
        raise DAVError(HTTP_INTERNAL_ERROR) from exc


def handle_error(func):
    operation = func.__name__

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        print(">>>>>>>", operation, self.fspath, args)
        try:
            with translate_error(event_bus=self.event_bus, operation=operation, path=self.fspath):
                ret = func(self, *args, **kwargs)
                print("<<<<<<<", ret)
                return ret
        except Exception as exc:
            import traceback

            traceback.print_exc()
            print("!!!!!!", exc)
            raise

    return wrapper


class FileResourceWritter:
    def __init__(self, event_bus, fspath, fs_access, fd):
        self.event_bus = event_bus
        self.fspath = fspath
        self.fs_access = fs_access
        self.fd = fd

    @handle_error
    def write(self, data):
        return self.fs_access.fd_write(self.fd, content=data, offset=-1)

    @handle_error
    def close(self):
        self.fs_access.fd_close(self.fd)


class FileResourceReader:
    def __init__(self, event_bus, fspath, fs_access, fd):
        self.offset = 0
        self.event_bus = event_bus
        self.fspath = fspath
        self.fs_access = fs_access
        self.fd = fd

    @handle_error
    def seek(self, offset):
        self.offset = offset

    @handle_error
    def read(self, size):
        ret = self.fs_access.fd_read(self.fd, offset=self.offset, size=size)
        self.offset += size
        return ret

    @handle_error
    def close(self):
        self.fs_access.fd_close(self.fd)


class FileResource(DAVNonCollection):
    def __init__(self, path: str, environ: dict, event_bus, fspath: FsPath, fs_access):
        self.event_bus = event_bus
        self.fspath = fspath
        self.fs_access = fs_access
        super().__init__(path, environ)

    @handle_error
    def get_content_length(self):
        stat = self.fs_access.entry_info(FsPath(self.path))
        return stat["size"]

    @handle_error
    def get_content_type(self):
        return guess_mime_type(self.path)

    @handle_error
    def get_content(self):
        _, fd = self.fs_access.file_open(self.fspath, mode="r")
        return FileResourceReader(self.event_bus, self.fspath, self.fs_access, fd)

    @handle_error
    def support_ranges(self):
        return True

    @handle_error
    def begin_write(self, content_type=None):
        _, fd = self.fs_access.file_open(self.fspath, mode="w")
        return FileResourceWritter(self.event_bus, self.fspath, self.fs_access, fd)

    @handle_error
    def delete(self):
        self.fs_access.file_delete(self.fspath)

    @handle_error
    def copy_move_single(self, dest_path, is_move):
        if is_move:
            self.fs_access.move(self.fspath, FsPath(dest_path))
        else:
            self.fs_access.copyfile(self.fspath, FsPath(dest_path))


class FolderResource(DAVCollection):
    def __init__(self, path: str, environ: dict, event_bus, fspath: FsPath, fs_access):
        self.event_bus = event_bus
        self.fspath = fspath
        self.fs_access = fs_access
        super().__init__(path, environ)

    @handle_error
    def create_empty_resource(self, name):
        self.fs_access.file_create(self.fspath / name, open=False)
        return self.get_member(name)

    @handle_error
    def create_collection(self, name):
        self.fs_access.folder_create(self.fspath / name)
        return self.get_member(name)

    @handle_error
    def get_member_names(self):
        stat = self.fs_access.entry_info(self.fspath)
        return stat["children"]

    @handle_error
    def get_member(self, name):
        fspath = self.fspath / name
        path = str(fspath)
        try:
            stat = self.fs_access.entry_info(fspath)
        except FSFileNotFoundError:
            return None
        if stat["type"] == "folder":
            return FolderResource(path, self.environ, self.event_bus, fspath, self.fs_access)
        else:
            return FileResource(path, self.environ, self.event_bus, fspath, self.fs_access)

    @handle_error
    def delete(self):
        self.fs_access.folder_delete(self.fspath)

    @handle_error
    def copy_move_single(self, dest_path, is_move):
        if is_move:
            self.fs_access.move(self.fspath, FsPath(dest_path))
        else:
            self.fs_access.copytree(self.fspath, FsPath(dest_path))


class ParsecDAVProvider(DAVProvider):
    def __init__(self, event_bus, fs_access):
        super().__init__()
        self.event_bus = event_bus
        self.fs_access = fs_access

    def get_resource_inst(self, path, environ):
        fspath = FsPath(path)
        print("get_resource_inst>>>>>", fspath)
        try:
            stat = self.fs_access.entry_info(fspath)
        except FSFileNotFoundError:
            return None
        if stat["type"] == "folder":
            return FolderResource(path, environ, self.event_bus, fspath, self.fs_access)
        else:
            return FileResource(path, environ, self.event_bus, fspath, self.fs_access)
