# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional

import trio

from parsec.api.data import UserProfile
from parsec.api.protocol import (
    DeviceID,
    DeviceName,
    HumanHandle,
    OrganizationID,
    ServerHandshake,
    UserID,
)
from parsec.api.transport import Transport
from parsec.api.version import ApiVersion
from parsec.backend.invite import Invitation
from parsec.crypto import PublicKey, VerifyKey


class BaseClientContext:
    __slots__ = ("transport", "handshake")

    def __init__(self, transport: Transport, handshake: ServerHandshake):
        self.transport = transport
        self.handshake = handshake

    @property
    def api_version(self) -> ApiVersion:
        return self.handshake.backend_api_version

    @property
    def handshake_type(self) -> str:
        return self.handshake.answer_type


class AuthenticatedClientContext(BaseClientContext):
    __slots__ = (
        "organization_id",
        "device_id",
        "human_handle",
        "profile",
        "public_key",
        "verify_key",
        "event_bus_ctx",
        "channels",
        "realms",
        "conn_id",
        "logger",
    )

    def __init__(
        self,
        transport: Transport,
        handshake: ServerHandshake,
        organization_id: OrganizationID,
        device_id: DeviceID,
        human_handle: Optional[HumanHandle],
        profile: UserProfile,
        public_key: PublicKey,
        verify_key: VerifyKey,
    ):
        super().__init__(transport, handshake)
        self.organization_id = organization_id
        self.profile = profile
        self.device_id = device_id
        self.human_handle = human_handle
        self.public_key = public_key
        self.verify_key = verify_key

        self.event_bus_ctx = None  # Overwritten in BackendApp.handle_client
        self.channels = trio.open_memory_channel(100)
        self.realms = set()

        self.conn_id = self.transport.conn_id
        self.logger = self.transport.logger = self.transport.logger.bind(
            conn_id=self.conn_id,
            handshake_type=self.handshake_type.value,
            organization_id=self.organization_id,
            device_id=self.device_id,
        )

    def __repr__(self):
        return f"AuthenticatedClientContext(org={self.organization_id}, device={self.device_id})"

    @property
    def user_id(self) -> UserID:
        return self.device_id.user_id

    @property
    def device_name(self) -> DeviceName:
        return self.device_id.device_name

    @property
    def send_events_channel(self):
        send_channel, _ = self.channels
        return send_channel

    @property
    def receive_events_channel(self):
        _, receive_channel = self.channels
        return receive_channel


class InvitedClientContext(BaseClientContext):
    __slots__ = ("organization_id", "invitation", "conn_id", "logger")

    def __init__(
        self,
        transport: Transport,
        handshake: ServerHandshake,
        organization_id: OrganizationID,
        invitation: Invitation,
    ):
        super().__init__(transport, handshake)
        self.organization_id = organization_id
        self.invitation = invitation

        self.conn_id = self.transport.conn_id
        self.logger = self.transport.logger = self.transport.logger.bind(
            conn_id=self.conn_id,
            handshake_type=self.handshake_type.value,
            organization_id=self.organization_id,
            invitation=self.invitation,
        )

    def __repr__(self):
        return f"InvitedClientContext(org={self.organization_id}, invitation={self.invitation})"


class APIV1_AnonymousClientContext(BaseClientContext):
    __slots__ = ("organization_id", "conn_id", "logger")

    def __init__(
        self, transport: Transport, handshake: ServerHandshake, organization_id: OrganizationID
    ):
        super().__init__(transport, handshake)
        self.organization_id = organization_id

        self.conn_id = self.transport.conn_id
        self.logger = self.transport.logger = self.transport.logger.bind(
            conn_id=self.conn_id,
            handshake_type=self.handshake_type.value,
            organization_id=self.organization_id,
        )

    def __repr__(self):
        return f"APIV1_AnonymousClientContext(org={self.organization_id})"


class APIV1_AdministrationClientContext(BaseClientContext):
    __slots__ = ("conn_id", "logger")

    def __init__(self, transport: Transport, handshake: ServerHandshake):
        super().__init__(transport, handshake)

        self.conn_id = self.transport.conn_id
        self.logger = self.transport.logger = self.transport.logger.bind(
            conn_id=self.conn_id, handshake_type=self.handshake_type.value
        )

    def __repr__(self):
        return f"APIV1_AdministrationClientContext()"
