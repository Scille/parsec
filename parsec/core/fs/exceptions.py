# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS
"""
Define all the FSError classes, using the following hierarchy:

    FSInternalError (Exception)
    FSError (Exception)
    +-- FSMiscError
    +-- FSOperationError (OSError)
        +-- FSLocalOperationError
        +-- FSRemoteOperationError

"""

import errno
import os

from parsec.core.fs.utils import ntstatus
from parsec.core.types import EntryID

# Base classes for all file system errors


class FSInternalError(Exception):
    """
    Base class for exceptions that are not meant to propagate out of the fs module
    """

    pass


class FSError(Exception):
    """
    Base class for all fs exceptions
    """

    pass


class FSMiscError(FSError):
    """
    Base class for exceptions exposed by the fs module that are not related to an operation
    """


class FSOperationError(OSError, FSError):
    """
    Base class for the exceptions that may be raised during the execution of an operation
    """

    ERRNO = None
    WINERROR = None
    NTSTATUS = None

    def __init__(self, message=None, filename=None, filename2=None):
        # Get the actual message and save it
        if message is None and self.ERRNO is not None:
            message = os.strerror(self.ERRNO)
        self.message = str(message)

        # Error with no standard errno
        if self.ERRNO is None:
            return super().__init__(message)

        # Cast filename arguments
        if filename is not None:
            filename = str(filename)
        if filename2 is not None:
            filename2 = str(filename)

        # Full OS error initialization
        self.ntstatus = self.NTSTATUS
        super().__init__(self.ERRNO, self.message, filename, self.WINERROR, filename2)

    def __str__(self):
        if self.filename2:
            return f"{self.message}: {self.filename} -> {self.filename2}"
        if self.filename:
            return f"{self.message}: {self.filename}"
        return self.message


class FSLocalOperationError(FSOperationError):
    """
    Used to represent "normal" error (e.g. opening a non-existing file,
    removing a non-empty folder etc.)
    """

    ERRNO = errno.EINVAL
    NTSTATUS = ntstatus.STATUS_INVALID_PARAMETER


class FSRemoteOperationError(FSOperationError):
    """
    Used to represent error in the underlaying layers (e.g. data inconsistency,
    data access refused by the backend etc.)
    """

    ERRNO = errno.EACCES
    NTSTATUS = ntstatus.STATUS_ACCESS_DENIED


# Misc errors


class FSWorkspaceNotFoundError(FSMiscError):
    pass


class FSWorkspaceTimestampedTooEarly(FSMiscError):
    pass


# Internal errors


class FSLocalMissError(FSInternalError):
    def __init__(self, id: EntryID):
        super().__init__(id)
        self.id = id


class FSFileConflictError(FSInternalError):
    pass


class FSReshapingRequiredError(FSInternalError):
    pass


class FSNoSynchronizationRequired(FSInternalError):
    pass


# Local operation errors


class FSPermissionError(FSLocalOperationError, PermissionError):
    ERRNO = errno.EACCES
    NTSTATUS = ntstatus.STATUS_ACCESS_DENIED


class FSNoAccessError(FSPermissionError):
    ERRNO = errno.EACCES
    NTSTATUS = ntstatus.STATUS_ACCESS_DENIED


class FSReadOnlyError(FSPermissionError):
    ERRNO = errno.EROFS
    NTSTATUS = ntstatus.STATUS_ACCESS_DENIED


class FSNotADirectoryError(FSLocalOperationError, NotADirectoryError):
    ERRNO = errno.ENOTDIR
    NTSTATUS = ntstatus.STATUS_NOT_A_DIRECTORY


class FSFileNotFoundError(FSLocalOperationError, FileNotFoundError):
    ERRNO = errno.ENOENT
    NTSTATUS = ntstatus.STATUS_OBJECT_NAME_NOT_FOUND


class FSCrossDeviceError(FSLocalOperationError):
    ERRNO = errno.EXDEV
    NTSTATUS = ntstatus.STATUS_NOT_SAME_DEVICE


class FSFileExistsError(FSLocalOperationError, FileExistsError):
    ERRNO = errno.EEXIST
    NTSTATUS = ntstatus.STATUS_OBJECT_NAME_COLLISION


class FSIsADirectoryError(FSLocalOperationError, IsADirectoryError):
    ERRNO = errno.EISDIR
    NTSTATUS = ntstatus.STATUS_FILE_IS_A_DIRECTORY


class FSDirectoryNotEmptyError(FSLocalOperationError):
    ERRNO = errno.ENOTEMPTY
    NTSTATUS = ntstatus.STATUS_DIRECTORY_NOT_EMPTY


class FSInvalidFileDescriptor(FSLocalOperationError):
    ERRNO = errno.EBADF
    NTSTATUS = ntstatus.STATUS_INVALID_HANDLE


class FSInvalidArgumentError(FSLocalOperationError):
    ERRNO = errno.EINVAL
    NTSTATUS = ntstatus.STATUS_INVALID_PARAMETER


class FSEndOfFileError(FSLocalOperationError):
    ERRNO = errno.EINVAL
    NTSTATUS = ntstatus.STATUS_END_OF_FILE


# Remote operation errors


class FSBackendOfflineError(FSRemoteOperationError):
    ERRNO = errno.EHOSTUNREACH
    NTSTATUS = ntstatus.STATUS_HOST_UNREACHABLE


class FSRemoteManifestNotFound(FSRemoteOperationError):
    pass


class FSRemoteManifestNotFoundBadVersion(FSRemoteManifestNotFound):
    pass


class FSRemoteManifestNotFoundBadTimestamp(FSRemoteManifestNotFound):
    pass


class FSRemoteBlockNotFound(FSRemoteOperationError):
    pass


class FSRemoteSyncError(FSRemoteOperationError):
    pass


class FSBadEncryptionRevision(FSRemoteOperationError):
    pass


class FSSharingNotAllowedError(FSRemoteOperationError):
    pass


class FSWorkspaceNoAccess(FSRemoteOperationError, PermissionError):
    pass


class FSWorkspaceNoReadAccess(FSWorkspaceNoAccess):
    pass


class FSWorkspaceNoWriteAccess(FSWorkspaceNoAccess):
    pass


class FSWorkspaceNotInMaintenance(FSRemoteOperationError):
    pass


class FSWorkspaceInMaintenance(FSRemoteOperationError):
    pass
