# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from uuid import UUID
from enum import Enum
from typing import Optional, Union, Tuple, Set, Dict

import trio

from parsec.crypto import VerifyKey, PublicKey
from parsec.event_bus import EventBusConnectionContext
from parsec.api.version import ApiVersion
from parsec.api.transport import Transport
from parsec.api.data import UserProfile
from parsec.api.protocol import (
    ServerHandshake,
    OrganizationID,
    UserID,
    DeviceName,
    DeviceID,
    HumanHandle,
    HandshakeType,
    APIV1_HandshakeType,
)
from parsec.backend.invite import Invitation


class BaseClientContext:
    __slots__ = ("transport", "handshake")

    def __init__(self, transport: Transport, handshake: ServerHandshake):
        self.transport = transport
        self.handshake = handshake

    @property
    def api_version(self) -> ApiVersion:
        return self.handshake.backend_api_version

    @property
    def handshake_type(self) -> Union[HandshakeType, APIV1_HandshakeType]:
        return self.handshake.answer_type


class AuthenticatedClientContext(BaseClientContext):
    __slots__ = (
        "organization_id",
        "device_id",
        "human_handle",
        "device_label",
        "profile",
        "public_key",
        "verify_key",
        "event_bus_ctx",
        "channels",
        "realms",
        "events_subscribed",
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
        device_label: Optional[str],
        profile: UserProfile,
        public_key: PublicKey,
        verify_key: VerifyKey,
    ):
        super().__init__(transport, handshake)
        self.organization_id = organization_id
        self.profile = profile
        self.device_id = device_id
        self.human_handle = human_handle
        self.device_label = device_label
        self.public_key = public_key
        self.verify_key = verify_key

        self.event_bus_ctx: EventBusConnectionContext
        self.channels = trio.open_memory_channel[Tuple[Enum, Dict[str, object]]](100)
        self.realms: Set[UUID] = set()
        self.events_subscribed = False

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
    def user_display(self) -> str:
        return str(self.human_handle or self.device_id.user_id)

    @property
    def device_display(self) -> str:
        return str(self.device_label or self.device_id.device_name)

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
