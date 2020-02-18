# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.data.base import (
    DataError,
    BaseData,
    BaseAPIData,
    BaseSchema,
    BaseSignedData,
    BaseAPISignedData,
    BaseSignedDataSchema,
)
from parsec.api.data.entry import EntryID, EntryIDField, EntryName, EntryNameField
from parsec.api.data.certif import (
    UserCertificateContent,
    DeviceCertificateContent,
    RevokedUserCertificateContent,
    RealmRoleCertificateContent,
)
from parsec.api.data.invite_claim import (
    UserClaimContent,
    DeviceClaimContent,
    DeviceClaimAnswerContent,
)
from parsec.api.data.message import (
    MessageContent,
    SharingGrantedMessageContent,
    SharingReencryptedMessageContent,
    SharingRevokedMessageContent,
    SharingGarbageCollectedMessageContent,
    PingMessageContent,
)
from parsec.api.data.manifest import (
    BlockID,
    BlockIDField,
    BlockAccess,
    WorkspaceEntry,
    Manifest,
    UserManifest,
    WorkspaceManifest,
    FolderManifest,
    FileManifest,
)


__api_data_version__ = (1, 0)


__all__ = (
    "__api_data_version__",
    # Base
    "DataError",
    "BaseData",
    "BaseAPIData",
    "BaseSchema",
    "BaseSignedData",
    "BaseAPISignedData",
    "BaseSignedDataSchema",
    # Entry
    "EntryID",
    "EntryIDField",
    "EntryName",
    "EntryNameField",
    # Certifs
    "UserCertificateContent",
    "DeviceCertificateContent",
    "RevokedUserCertificateContent",
    "RealmRoleCertificateContent",
    # Invite&Claim
    "UserClaimContent",
    "DeviceClaimContent",
    "DeviceClaimAnswerContent",
    # Messages
    "MessageContent",
    "SharingGrantedMessageContent",
    "SharingReencryptedMessageContent",
    "SharingRevokedMessageContent",
    "SharingGarbageCollectedMessageContent",
    "PingMessageContent",
    # Manifests
    "BlockID",
    "BlockIDField",
    "BlockAccess",
    "WorkspaceEntry",
    "Manifest",
    "UserManifest",
    "WorkspaceManifest",
    "FolderManifest",
    "FileManifest",
)
