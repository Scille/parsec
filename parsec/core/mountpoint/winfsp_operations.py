# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import functools
from contextlib import contextmanager

from structlog import get_logger
from trio import Cancelled, RunFinishedError
from winfspy import (
    CREATE_FILE_CREATE_OPTIONS,
    FILE_ATTRIBUTE,
    BaseFileSystemOperations,
    NTStatusError,
)
from winfspy.plumbing.winstuff import NTSTATUS, SecurityDescriptor, dt_to_filetime

from parsec.core.core_events import CoreEvent
from parsec.core.fs import FSLocalOperationError, FSRemoteOperationError
from parsec.core.fs.workspacefs.sync_transactions import DEFAULT_BLOCK_SIZE
from parsec.core.mountpoint.winify import unwinify_entry_name, winify_entry_name
from parsec.core.types import FsPath

logger = get_logger()

# Taken from https://docs.microsoft.com/en-us/windows/win32/fileio/file-access-rights-constants
FILE_READ_DATA = 1 << 0
FILE_WRITE_DATA = 1 << 1


def _winpath_to_parsec(path: str) -> FsPath:
    # Given / is not allowed, no need to check if path already contains it
    return FsPath(unwinify_entry_name(path.replace("\\", "/")))


def _parsec_to_winpath(path: FsPath) -> str:
    return "\\" + "\\".join(winify_entry_name(entry) for entry in path.parts)


@contextmanager
def translate_error(event_bus, operation, path):
    try:
        yield

    except NTStatusError:
        raise

    except FSLocalOperationError as exc:
        raise NTStatusError(exc.ntstatus) from exc

    except FSRemoteOperationError as exc:
        event_bus.send(CoreEvent.MOUNTPOINT_REMOTE_ERROR, exc=exc, operation=operation, path=path)
        raise NTStatusError(exc.ntstatus) from exc

    except (Cancelled, RunFinishedError) as exc:
        # WinFSP teardown operation doesn't make sure no concurrent operation
        # are running
        raise NTStatusError(NTSTATUS.STATUS_NO_SUCH_DEVICE) from exc

    except Exception as exc:
        logger.exception("Unhandled exception in winfsp mountpoint", operation=operation, path=path)
        event_bus.send(
            CoreEvent.MOUNTPOINT_UNHANDLED_ERROR, exc=exc, operation=operation, path=path
        )
        raise NTStatusError(NTSTATUS.STATUS_INTERNAL_ERROR) from exc


def round_to_block_size(size, block_size=DEFAULT_BLOCK_SIZE):
    if size % block_size == 0:
        return size
    return ((size // block_size) + 1) * block_size


def stat_to_file_attributes(stat):
    # TODO: consider using FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS/FILE_ATTRIBUTE_RECALL_ON_OPEN ?
    # (see https://docs.microsoft.com/en-us/windows/desktop/fileio/file-attribute-constants)
    if stat["type"] == "folder":
        return FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
    else:
        return FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL


def stat_to_winfsp_attributes(stat):
    created = dt_to_filetime(stat["created"])
    updated = dt_to_filetime(stat["updated"])
    attributes = {
        "creation_time": created,
        "last_access_time": updated,
        "last_write_time": updated,
        "change_time": updated,
        "index_number": stat["id"].int & 0xFFFFFFFF,  # uint64_t
    }

    if stat["type"] == "folder":
        attributes["file_attributes"] = FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
        attributes["allocation_size"] = 0
        attributes["file_size"] = 0

    else:
        # FILE_ATTRIBUTE_ARCHIVE is a good default attribute
        # This way, we don't need to deal with the weird semantics of
        # FILE_ATTRIBUTE_NORMAL which means "no other attributes is set"
        # Also, this is what the winfsp memfs does.
        attributes["file_attributes"] = FILE_ATTRIBUTE.FILE_ATTRIBUTE_ARCHIVE
        attributes["allocation_size"] = round_to_block_size(stat["size"])
        attributes["file_size"] = stat["size"]

    # Disable content indexing for all files and directories
    attributes["file_attributes"] |= FILE_ATTRIBUTE.FILE_ATTRIBUTE_NOT_CONTENT_INDEXED

    return attributes


class OpenedFolder:
    def __init__(self, path):
        self.path = path
        self.deleted = False

    def is_root(self):
        return self.path.is_root()


class OpenedFile:
    def __init__(self, path, fd):
        self.path = path
        self.fd = fd
        self.deleted = False


def handle_error(func):
    """A decorator to handle error in wfspy operations"""
    operation = func.__name__

    @functools.wraps(func)
    def wrapper(self, arg, *args, **kwargs):
        path = arg.path if isinstance(arg, (OpenedFile, OpenedFolder)) else _winpath_to_parsec(arg)
        with translate_error(self.event_bus, operation, path):
            return func.__get__(self)(arg, *args, **kwargs)

    return wrapper


class WinFSPOperations(BaseFileSystemOperations):
    def __init__(self, event_bus, volume_label, fs_access):
        super().__init__()
        # see https://docs.microsoft.com/fr-fr/windows/desktop/SecAuthZ/security-descriptor-string-format  # noqa
        self._security_descriptor = SecurityDescriptor.from_string(
            # "O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;WD)"
            "O:BAG:BAD:NO_ACCESS_CONTROL"
        )
        self.event_bus = event_bus
        self.fs_access = fs_access

        max_file_nodes = 1024
        max_file_size = 16 * 1024 * 1024
        file_nodes = 1
        self._volume_info = {
            "total_size": max_file_nodes * max_file_size,
            "free_size": (max_file_nodes - file_nodes) * max_file_size,
            "volume_label": volume_label,
        }

    def get_volume_info(self):
        return self._volume_info

    def set_volume_label(self, volume_label):
        self._volume_info["volume_label"] = volume_label

    @handle_error
    def get_security_by_name(self, file_name):
        file_name = _winpath_to_parsec(file_name)
        stat = self.fs_access.entry_info(file_name)
        return (
            stat_to_file_attributes(stat),
            self._security_descriptor.handle,
            self._security_descriptor.size,
        )

    @handle_error
    def create(
        self,
        file_name,
        create_options,
        granted_access,
        file_attributes,
        security_descriptor,
        allocation_size,
    ):
        # `granted_access` is already handle by winfsp
        # `allocation_size` useless for us
        # `security_descriptor` is not supported yet
        file_name = _winpath_to_parsec(file_name)

        if create_options & CREATE_FILE_CREATE_OPTIONS.FILE_DIRECTORY_FILE:
            self.fs_access.folder_create(file_name)
            return OpenedFolder(file_name)

        else:
            _, fd = self.fs_access.file_create(file_name, open=True)
            return OpenedFile(file_name, fd)

    @handle_error
    def get_security(self, file_context):
        return self._security_descriptor.handle, self._security_descriptor.size

    @handle_error
    def set_security(self, file_context, security_information, modification_descriptor):
        # TODO
        pass

    @handle_error
    def rename(self, file_context, file_name, new_file_name, replace_if_exists):
        file_name = _winpath_to_parsec(file_name)
        new_file_name = _winpath_to_parsec(new_file_name)
        self.fs_access.entry_rename(file_name, new_file_name, overwrite=replace_if_exists)

    @handle_error
    def open(self, file_name, create_options, granted_access):
        file_name = _winpath_to_parsec(file_name)
        granted_access = granted_access & (FILE_READ_DATA | FILE_WRITE_DATA)
        if granted_access == FILE_READ_DATA:
            mode = "r"
        elif granted_access == FILE_WRITE_DATA:
            mode = "w"
        elif granted_access == FILE_READ_DATA | FILE_WRITE_DATA:
            mode = "rw"
        else:
            mode = "r"
        # `granted_access` is already handle by winfsp
        try:
            _, fd = self.fs_access.file_open(file_name, mode=mode)
            return OpenedFile(file_name, fd)
        except IsADirectoryError:
            return OpenedFolder(file_name)

    @handle_error
    def close(self, file_context):
        if isinstance(file_context, OpenedFile):
            self.fs_access.fd_close(file_context.fd)

        if file_context.deleted:
            if isinstance(file_context, OpenedFile):
                self.fs_access.file_delete(file_context.path)
            else:
                self.fs_access.folder_delete(file_context.path)

    @handle_error
    def get_file_info(self, file_context):
        stat = self.fs_access.entry_info(file_context.path)
        return stat_to_winfsp_attributes(stat)

    @handle_error
    def set_basic_info(
        self,
        file_context,
        file_attributes,
        creation_time,
        last_access_time,
        last_write_time,
        change_time,
        file_info,
    ) -> dict:
        # TODO

        # file_obj = file_context.file_obj
        # if file_attributes != FILE_ATTRIBUTE.INVALID_FILE_ATTRIBUTES:
        #     file_obj.file_attributes = file_attributes
        # if creation_time:
        #     file_obj.creation_time = creation_time
        # if last_access_time:
        #     file_obj.last_access_time = last_access_time
        # if last_write_time:
        #     file_obj.last_write_time = last_write_time
        # if change_time:
        #     file_obj.change_time = change_time

        return self.get_file_info(file_context)

    @handle_error
    def set_file_size(self, file_context, new_size, set_allocation_size):
        self.fs_access.fd_resize(file_context.fd, new_size, truncate_only=set_allocation_size)

    @handle_error
    def can_delete(self, file_context, file_name: str) -> None:
        self.fs_access.check_write_rights(file_context.path)
        stat = self.fs_access.entry_info(file_context.path)
        if stat["type"] == "file":
            return
        if file_context.is_root():
            # Cannot remove root mountpoint !
            raise NTStatusError(NTSTATUS.STATUS_RESOURCEMANAGER_READ_ONLY)
        if stat["children"]:
            raise NTStatusError(NTSTATUS.STATUS_DIRECTORY_NOT_EMPTY)

    @handle_error
    def read_directory(self, file_context, marker):
        entries = []
        stat = self.fs_access.entry_info(file_context.path)

        if stat["type"] == "file":
            raise NTStatusError(NTSTATUS.STATUS_NOT_A_DIRECTORY)

        # NOTE: The "." and ".." directories should ONLY be included
        # if the queried directory is not root

        # Current directory
        if marker is None and not file_context.path.is_root():
            entry = {"file_name": ".", **stat_to_winfsp_attributes(stat)}
            entries.append(entry)
        elif marker == ".":
            marker = None

        # Parent directory
        if marker is None and not file_context.path.is_root():
            parent_stat = self.fs_access.entry_info(file_context.path.parent)
            entry = {"file_name": "..", **stat_to_winfsp_attributes(parent_stat)}
            entries.append(entry)
        elif marker == "..":
            marker = None

        # NOTE: we *do not* rely on alphabetically sorting to compare the
        # marker given `..` is always the first element event if we could
        # have children name before it (`.-foo` for instance)
        iter_children_names = iter(stat["children"])
        if marker is not None:
            for child_name in iter_children_names:
                if child_name == marker:
                    break

        # All remaining children are located after the marker
        for child_name in iter_children_names:
            name = winify_entry_name(child_name)
            child_stat = self.fs_access.entry_info(file_context.path / child_name)
            entry = {"file_name": name, **stat_to_winfsp_attributes(child_stat)}
            entries.append(entry)

        return entries

    @handle_error
    def get_dir_info_by_name(self, file_context, file_name):
        child_name = unwinify_entry_name(file_name)
        stat = self.fs_access.entry_info(file_context.path / child_name)
        entry = {"file_name": file_name, **stat_to_winfsp_attributes(stat)}
        return entry

    @handle_error
    def read(self, file_context, offset, length):
        buffer = self.fs_access.fd_read(file_context.fd, length, offset, raise_eof=True)
        return buffer

    @handle_error
    def write(self, file_context, buffer, offset, write_to_end_of_file, constrained_io):
        # Adapt offset
        if write_to_end_of_file:
            offset = -1
        # LocalStorage.set only wants bytes or bytearray, not a cffi buffer
        buffer = bytes(buffer)
        # Atomic write
        return self.fs_access.fd_write(file_context.fd, buffer, offset, constrained_io)

    @handle_error
    def flush(self, file_context) -> None:
        self.fs_access.fd_flush(file_context.fd)

    @handle_error
    def cleanup(self, file_context, file_name, flags) -> None:
        # FspCleanupDelete
        if flags & 1:
            self.fs_access.check_write_rights(file_context.path)
            file_context.deleted = True

    @handle_error
    def overwrite(
        self, file_context, file_attributes, replace_file_attributes: bool, allocation_size: int
    ) -> None:
        self.fs_access.fd_resize(file_context.fd, allocation_size, truncate_only=True)
