# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import NewType, Union

from parsec.api.data import EntryID, EntryIDField, EntryName, EntryNameField
from parsec.core.types.backend_address import (
    BackendActionAddr,
    BackendAddr,
    BackendInvitationAddr,
    BackendOrganizationAddr,
    BackendOrganizationAddrField,
    BackendOrganizationBootstrapAddr,
    BackendOrganizationClaimDeviceAddr,
    BackendOrganizationClaimUserAddr,
    BackendOrganizationFileLinkAddr,
)
from parsec.core.types.base import FsPath
from parsec.core.types.local_device import DeviceInfo, LocalDevice, UserInfo
from parsec.core.types.manifest import (
    DEFAULT_BLOCK_SIZE,
    BlockAccess,
    BlockID,
    Chunk,
    ChunkID,
    LocalFileManifest,
    LocalFolderManifest,
    LocalManifest,
    LocalUserManifest,
    LocalWorkspaceManifest,
    WorkspaceEntry,
    WorkspaceRole,
)

FileDescriptor = NewType("FileDescriptor", int)
LocalFolderishManifests = Union[LocalFolderManifest, LocalWorkspaceManifest]


__all__ = (
    "FileDescriptor",
    "LocalFolderishManifests",
    # Base
    "FsPath",
    # Entry
    "EntryID",
    "EntryIDField",
    "EntryName",
    "EntryNameField",
    # Backend address
    "BackendAddr",
    "BackendOrganizationAddr",
    "BackendActionAddr",
    "BackendOrganizationBootstrapAddr",
    "BackendOrganizationClaimUserAddr",
    "BackendOrganizationClaimDeviceAddr",
    "BackendOrganizationAddrField",
    "BackendOrganizationFileLinkAddr",
    "BackendInvitationAddr",
    # local_device
    "LocalDevice",
    "UserInfo",
    "DeviceInfo",
    # "manifest"
    "DEFAULT_BLOCK_SIZE",
    "LocalFileManifest",
    "LocalFolderManifest",
    "LocalWorkspaceManifest",
    "LocalUserManifest",
    "LocalManifest",
    "WorkspaceEntry",
    "WorkspaceRole",
    "BlockAccess",
    "BlockID",
    "Chunk",
    "ChunkID",
)
