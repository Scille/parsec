# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.protocol.base import (
    ProtocolError,
    MessageSerializationError,
    InvalidMessageError,
    packb,
    unpackb,
)
from parsec.api.protocol.types import (
    UserID,
    DeviceID,
    DeviceName,
    OrganizationID,
    UserIDField,
    DeviceIDField,
    DeviceNameField,
    OrganizationIDField,
)
from parsec.api.protocol.handshake import (
    HandshakeError,
    HandshakeFailedChallenge,
    HandshakeBadIdentity,
    HandshakeOrganizationExpired,
    HandshakeRevokedDevice,
    HandshakeAPIVersionError,
    HandshakeRVKMismatch,
    ServerHandshake,
    AuthenticatedClientHandshake,
    AnonymousClientHandshake,
    AdministrationClientHandshake,
)
from parsec.api.protocol.organization import (
    organization_create_serializer,
    organization_bootstrap_serializer,
    organization_stats_serializer,
    organization_status_serializer,
)
from parsec.api.protocol.events import events_subscribe_serializer, events_listen_serializer
from parsec.api.protocol.ping import ping_serializer
from parsec.api.protocol.user import (
    user_get_serializer,
    user_find_serializer,
    user_invite_serializer,
    user_get_invitation_creator_serializer,
    user_claim_serializer,
    user_cancel_invitation_serializer,
    user_create_serializer,
    user_revoke_serializer,
    device_invite_serializer,
    device_get_invitation_creator_serializer,
    device_claim_serializer,
    device_cancel_invitation_serializer,
    device_create_serializer,
)
from parsec.api.protocol.message import message_get_serializer
from parsec.api.protocol.realm import (
    RealmRole,
    RealmRoleField,
    MaintenanceType,
    MaintenanceTypeField,
    realm_create_serializer,
    realm_status_serializer,
    realm_get_role_certificates_serializer,
    realm_update_roles_serializer,
    realm_start_reencryption_maintenance_serializer,
    realm_finish_reencryption_maintenance_serializer,
    realm_start_garbage_collection_maintenance_serializer,
    realm_finish_garbage_collection_maintenance_serializer,
)
from parsec.api.protocol.block import block_create_serializer, block_read_serializer
from parsec.api.protocol.vlob import (
    vlob_create_serializer,
    vlob_read_serializer,
    vlob_update_serializer,
    vlob_poll_changes_serializer,
    vlob_list_versions_serializer,
    vlob_maintenance_get_reencryption_batch_serializer,
    vlob_maintenance_save_reencryption_batch_serializer,
    vlob_maintenance_get_garbage_collection_batch_serializer,
    vlob_maintenance_save_garbage_collection_batch_serializer,
)


__all__ = (
    "ProtocolError",
    "MessageSerializationError",
    "InvalidMessageError",
    "packb",
    "unpackb",
    "HandshakeError",
    "HandshakeFailedChallenge",
    "HandshakeBadIdentity",
    "HandshakeOrganizationExpired",
    "HandshakeRevokedDevice",
    "HandshakeAPIVersionError",
    "HandshakeRVKMismatch",
    "ServerHandshake",
    "AuthenticatedClientHandshake",
    "AnonymousClientHandshake",
    "AdministrationClientHandshake",
    # Types
    "UserID",
    "DeviceID",
    "DeviceName",
    "OrganizationID",
    "UserIDField",
    "DeviceIDField",
    "DeviceNameField",
    "OrganizationIDField",
    # Organization
    "organization_create_serializer",
    "organization_bootstrap_serializer",
    "organization_stats_serializer",
    "organization_status_serializer",
    # Events
    "events_subscribe_serializer",
    "events_listen_serializer",
    # Ping
    "ping_serializer",
    # User
    "user_get_serializer",
    "user_find_serializer",
    "user_invite_serializer",
    "user_get_invitation_creator_serializer",
    "user_claim_serializer",
    "user_cancel_invitation_serializer",
    "user_create_serializer",
    "user_revoke_serializer",
    "device_invite_serializer",
    "device_get_invitation_creator_serializer",
    "device_claim_serializer",
    "device_cancel_invitation_serializer",
    "device_create_serializer",
    # Message
    "message_get_serializer",
    # Data group
    "RealmRole",
    "RealmRoleField",
    "MaintenanceType",
    "MaintenanceTypeField",
    "realm_create_serializer",
    "realm_status_serializer",
    "realm_get_role_certificates_serializer",
    "realm_update_roles_serializer",
    "realm_start_reencryption_maintenance_serializer",
    "realm_finish_reencryption_maintenance_serializer",
    "realm_start_garbage_collection_maintenance_serializer",
    "realm_finish_garbage_collection_maintenance_serializer",
    # Vlob
    "vlob_create_serializer",
    "vlob_read_serializer",
    "vlob_update_serializer",
    "vlob_poll_changes_serializer",
    "vlob_list_versions_serializer",
    "vlob_maintenance_get_reencryption_batch_serializer",
    "vlob_maintenance_save_reencryption_batch_serializer",
    "vlob_maintenance_get_garbage_collection_batch_serializer",
    "vlob_maintenance_save_garbage_collection_batch_serializer",
    # Block
    "block_create_serializer",
    "block_read_serializer",
)
