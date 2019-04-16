# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.protocole.base import (
    ProtocoleError,
    MessageSerializationError,
    InvalidMessageError,
    packb,
    unpackb,
)
from parsec.api.protocole.handshake import (
    HandshakeError,
    HandshakeFailedChallenge,
    HandshakeBadIdentity,
    HandshakeRevokedDevice,
    HandshakeAPIVersionError,
    ServerHandshake,
    AuthenticatedClientHandshake,
    AnonymousClientHandshake,
    AdministrationClientHandshake,
)
from parsec.api.protocole.organization import (
    organization_create_serializer,
    organization_bootstrap_serializer,
)
from parsec.api.protocole.events import events_subscribe_serializer, events_listen_serializer
from parsec.api.protocole.ping import ping_serializer
from parsec.api.protocole.message import message_send_serializer, message_get_serializer
from parsec.api.protocole.block import block_create_serializer, block_read_serializer
from parsec.api.protocole.vlob import (
    vlob_group_check_serializer,
    vlob_create_serializer,
    vlob_read_serializer,
    vlob_update_serializer,
    vlob_group_update_rights_serializer,
    vlob_group_get_rights_serializer,
    vlob_group_poll_serializer,
)
from parsec.api.protocole.user import (
    user_get_serializer,
    user_find_serializer,
    user_invite_serializer,
    user_get_invitation_creator_serializer,
    user_claim_serializer,
    user_cancel_invitation_serializer,
    user_create_serializer,
    device_invite_serializer,
    device_get_invitation_creator_serializer,
    device_claim_serializer,
    device_cancel_invitation_serializer,
    device_create_serializer,
    device_revoke_serializer,
)


__all__ = (
    "ProtocoleError",
    "MessageSerializationError",
    "InvalidMessageError",
    "packb",
    "unpackb",
    "HandshakeError",
    "HandshakeFailedChallenge",
    "HandshakeBadIdentity",
    "HandshakeRevokedDevice",
    "ServerHandshake",
    "AuthenticatedClientHandshake",
    "AnonymousClientHandshake",
    "AdministrationClientHandshake",
    # Organization
    "organization_create_serializer",
    "organization_bootstrap_serializer",
    # Events
    "events_subscribe_serializer",
    "events_listen_serializer",
    # Ping
    "ping_serializer",
    # Message
    "message_send_serializer",
    "message_get_serializer",
    # Block
    "block_create_serializer",
    "block_read_serializer",
    # Vlob
    "vlob_group_check_serializer",
    "vlob_create_serializer",
    "vlob_read_serializer",
    "vlob_update_serializer",
    "vlob_group_update_rights_serializer",
    "vlob_group_get_rights_serializer",
    "vlob_group_poll_serializer",
    # User
    "user_get_serializer",
    "user_find_serializer",
    "user_invite_serializer",
    "user_get_invitation_creator_serializer",
    "user_claim_serializer",
    "user_cancel_invitation_serializer",
    "user_create_serializer",
    "device_invite_serializer",
    "device_get_invitation_creator_serializer",
    "device_claim_serializer",
    "device_cancel_invitation_serializer",
    "device_create_serializer",
    "device_revoke_serializer",
)
