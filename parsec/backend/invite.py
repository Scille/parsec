# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from collections import defaultdict
from enum import Enum
from typing import Dict, List, Optional, Set, Union
from uuid import UUID, uuid4

import attr
from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data import UserProfile
from parsec.api.protocol import (
    HandshakeType,
    HumanHandle,
    InvitationDeletedReason,
    InvitationStatus,
    InvitationType,
    OrganizationID,
    UserID,
    invite_1_claimer_wait_peer_serializer,
    invite_1_greeter_wait_peer_serializer,
    invite_2a_claimer_send_hashed_nonce_serializer,
    invite_2a_greeter_get_hashed_nonce_serializer,
    invite_2b_claimer_send_nonce_serializer,
    invite_2b_greeter_send_nonce_serializer,
    invite_3a_claimer_signify_trust_serializer,
    invite_3a_greeter_wait_peer_trust_serializer,
    invite_3b_claimer_wait_peer_trust_serializer,
    invite_3b_greeter_signify_trust_serializer,
    invite_4_claimer_communicate_serializer,
    invite_4_greeter_communicate_serializer,
    invite_delete_serializer,
    invite_info_serializer,
    invite_list_serializer,
    invite_new_serializer,
)
from parsec.backend.backend_events import BackendEvent
from parsec.backend.utils import api, catch_protocol_errors
from parsec.crypto import PublicKey
from parsec.event_bus import EventBus

PEER_EVENT_MAX_WAIT = 300  # 5mn


class InvitationError(Exception):
    pass


class InvitationNotFoundError(InvitationError):
    pass


class InvitationAlreadyDeletedError(InvitationError):
    pass


class InvitationInvalidStateError(InvitationError):
    pass


class ConduitState(Enum):
    STATE_1_WAIT_PEERS = "1_WAIT_PEERS"
    STATE_2_1_CLAIMER_HASHED_NONCE = "2_1_CLAIMER_HASHED_NONCE"
    STATE_2_2_GREETER_NONCE = "2_2_GREETER_NONCE"
    STATE_2_3_CLAIMER_NONCE = "2_3_CLAIMER_NONCE"
    STATE_3_1_CLAIMER_TRUST = "3_1_CLAIMER_TRUST"
    STATE_3_2_GREETER_TRUST = "3_2_GREETER_TRUST"
    STATE_4_COMMUNICATE = "4_COMMUNICATE"


NEXT_CONDUIT_STATE = {
    ConduitState.STATE_1_WAIT_PEERS: ConduitState.STATE_2_1_CLAIMER_HASHED_NONCE,
    ConduitState.STATE_2_1_CLAIMER_HASHED_NONCE: ConduitState.STATE_2_2_GREETER_NONCE,
    ConduitState.STATE_2_2_GREETER_NONCE: ConduitState.STATE_2_3_CLAIMER_NONCE,
    ConduitState.STATE_2_3_CLAIMER_NONCE: ConduitState.STATE_3_1_CLAIMER_TRUST,
    ConduitState.STATE_3_1_CLAIMER_TRUST: ConduitState.STATE_3_2_GREETER_TRUST,
    ConduitState.STATE_3_2_GREETER_TRUST: ConduitState.STATE_4_COMMUNICATE,
    ConduitState.STATE_4_COMMUNICATE: ConduitState.STATE_4_COMMUNICATE,
}


@attr.s(slots=True, frozen=True, auto_attribs=True)
class ConduitListenCtx:
    organization_id: OrganizationID
    greeter: Optional[UserID]
    token: UUID
    state: ConduitState
    payload: bytes
    peer_payload: Optional[bytes]

    @property
    def is_greeter(self):
        return self.greeter is not None


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserInvitation:
    greeter_user_id: UserID
    greeter_human_handle: Optional[HumanHandle]
    claimer_email: str
    token: UUID = attr.ib(factory=uuid4)
    created_on: Pendulum = attr.ib(factory=pendulum_now)
    status: InvitationStatus = InvitationStatus.IDLE

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceInvitation:
    greeter_user_id: UserID
    greeter_human_handle: Optional[HumanHandle]
    token: UUID = attr.ib(factory=uuid4)
    created_on: Pendulum = attr.ib(factory=pendulum_now)
    status: InvitationStatus = InvitationStatus.IDLE

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)


Invitation = Union[UserInvitation, DeviceInvitation]


class BaseInviteComponent:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        # We use the `invite.status_changed` event to keep a list of all the
        # invitation claimers connected accross all backends.
        #
        # This is useful to display the invitations ready to be greeted.
        # Note we rely on a per-backend list in memory instead of storing this
        # information in database so that we default to no claimer present
        # (which is the most likely when a backend is restarted) .
        #
        # However there is multiple way this list can go out of sync:
        # - a claimer can be connected to a backend, then another backend starts
        # - the backend the claimer is connected to crashes witout being able
        #   to notify the other backends
        # - a claimer open multiple connections at the same time, then is
        #   considered disconnected as soon as it close one of it connections
        #
        # This is considered "fine enough" given all the claimer has to do
        # to fix this is to retry a connection, which precisely the kind of
        # "I.T., have you tried to turn it off and on again ?" a human is
        # expected to do ;-)
        self._claimers_ready: Dict[OrganizationID, Set[UUID]] = defaultdict(set)

        def _on_status_changed(event, organization_id, greeter, token, status):
            if status == InvitationStatus.READY:
                self._claimers_ready[organization_id].add(token)
            else:  # Invitation deleted or back to idle
                self._claimers_ready[organization_id].discard(token)

        self._event_bus.connect(BackendEvent.INVITE_STATUS_CHANGED, _on_status_changed)

    @api("invite_new", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_new(self, client_ctx, msg):
        msg = invite_new_serializer.req_load(msg)

        if msg["type"] == InvitationType.USER:
            if client_ctx.profile != UserProfile.ADMIN:
                return invite_new_serializer.rep_dump({"status": "not_allowed"})

            # TODO: implement send email feature
            if msg["send_email"]:
                return invite_new_serializer.rep_dump({"status": "not_implemented"})

            invitation = await self.new_for_user(
                organization_id=client_ctx.organization_id,
                greeter_user_id=client_ctx.user_id,
                claimer_email=msg["claimer_email"],
            )

        else:  # Device
            invitation = await self.new_for_device(
                organization_id=client_ctx.organization_id, greeter_user_id=client_ctx.user_id
            )

        return invite_new_serializer.rep_dump({"status": "ok", "token": invitation.token})

    @api("invite_delete", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_delete(self, client_ctx, msg):
        msg = invite_delete_serializer.req_load(msg)
        try:
            await self.delete(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                on=pendulum_now(),
                reason=msg["reason"],
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        return invite_delete_serializer.rep_dump({"status": "ok"})

    @api("invite_list", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_list(self, client_ctx, msg):
        msg = invite_list_serializer.req_load(msg)
        invitations = await self.list(
            organization_id=client_ctx.organization_id, greeter=client_ctx.user_id
        )
        return invite_list_serializer.rep_dump(
            {
                "invitations": [
                    {
                        "type": InvitationType.USER
                        if isinstance(item, UserInvitation)
                        else InvitationType.DEVICE,
                        "token": item.token,
                        "created_on": item.created_on,
                        "claimer_email": getattr(
                            item, "claimer_email", None
                        ),  # Only available for user
                        "status": item.status,
                    }
                    for item in invitations
                ]
            }
        )

    @api("invite_info", handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_info(self, client_ctx, msg):
        invite_info_serializer.req_load(msg)
        # Invitation has already been fetched during handshake
        invitation = client_ctx.invitation
        # TODO: check invitation status and close connection if deleted ?
        if isinstance(invitation, UserInvitation):
            rep = {
                "type": InvitationType.USER,
                "claimer_email": invitation.claimer_email,
                "greeter_user_id": invitation.greeter_user_id,
                "greeter_human_handle": invitation.greeter_human_handle,
            }
        else:  # DeviceInvitation
            rep = {
                "type": InvitationType.DEVICE,
                "greeter_user_id": invitation.greeter_user_id,
                "greeter_human_handle": invitation.greeter_human_handle,
            }
        return invite_info_serializer.rep_dump(rep)

    @api("invite_1_claimer_wait_peer", handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_1_claimer_wait_peer(self, client_ctx, msg):
        msg = invite_1_claimer_wait_peer_serializer.req_load(msg)

        try:
            greeter_public_key = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_1_WAIT_PEERS,
                payload=msg["claimer_public_key"].encode(),
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_1_claimer_wait_peer_serializer.rep_dump(
            {"status": "ok", "greeter_public_key": PublicKey(greeter_public_key)}
        )

    @api("invite_1_greeter_wait_peer", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_1_greeter_wait_peer(self, client_ctx, msg):
        msg = invite_1_greeter_wait_peer_serializer.req_load(msg)

        try:
            claimer_public_key_raw = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                state=ConduitState.STATE_1_WAIT_PEERS,
                payload=msg["greeter_public_key"].encode(),
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_1_greeter_wait_peer_serializer.rep_dump(
            {"status": "ok", "claimer_public_key": PublicKey(claimer_public_key_raw)}
        )

    @api("invite_2a_claimer_send_hashed_nonce", handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_2a_claimer_send_hashed_nonce(self, client_ctx, msg):
        msg = invite_2a_claimer_send_hashed_nonce_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_2_1_CLAIMER_HASHED_NONCE,
                payload=msg["claimer_hashed_nonce"],
            )

            greeter_nonce = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_2_2_GREETER_NONCE,
                payload=b"",
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_2a_claimer_send_hashed_nonce_serializer.rep_dump(
            {"status": "ok", "greeter_nonce": greeter_nonce}
        )

    @api("invite_2a_greeter_get_hashed_nonce", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_2a_greeter_get_hashed_nonce(self, client_ctx, msg):
        msg = invite_2a_greeter_get_hashed_nonce_serializer.req_load(msg)

        try:
            claimer_hashed_nonce = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                state=ConduitState.STATE_2_1_CLAIMER_HASHED_NONCE,
                payload=b"",
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_2a_greeter_get_hashed_nonce_serializer.rep_dump(
            {"status": "ok", "claimer_hashed_nonce": claimer_hashed_nonce}
        )

    @api("invite_2b_greeter_send_nonce", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_2b_greeter_send_nonce(self, client_ctx, msg):
        msg = invite_2b_greeter_send_nonce_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                state=ConduitState.STATE_2_2_GREETER_NONCE,
                payload=msg["greeter_nonce"],
            )

            claimer_nonce = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                state=ConduitState.STATE_2_3_CLAIMER_NONCE,
                payload=b"",
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_2b_greeter_send_nonce_serializer.rep_dump(
            {"status": "ok", "claimer_nonce": claimer_nonce}
        )

    @api("invite_2b_claimer_send_nonce", handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_2b_claimer_send_nonce(self, client_ctx, msg):
        msg = invite_2b_claimer_send_nonce_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_2_3_CLAIMER_NONCE,
                payload=msg["claimer_nonce"],
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_2b_claimer_send_nonce_serializer.rep_dump({"status": "ok"})

    @api("invite_3a_greeter_wait_peer_trust", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_3a_greeter_wait_peer_trust(self, client_ctx, msg):
        msg = invite_3a_greeter_wait_peer_trust_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                state=ConduitState.STATE_3_1_CLAIMER_TRUST,
                payload=b"",
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_3a_greeter_wait_peer_trust_serializer.rep_dump({"status": "ok"})

    @api("invite_3b_claimer_wait_peer_trust", handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_3b_claimer_wait_peer_trust(self, client_ctx, msg):
        msg = invite_3b_claimer_wait_peer_trust_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_3_2_GREETER_TRUST,
                payload=b"",
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_3b_claimer_wait_peer_trust_serializer.rep_dump({"status": "ok"})

    @api("invite_3b_greeter_signify_trust", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_3b_greeter_signify_trust(self, client_ctx, msg):
        msg = invite_3b_greeter_signify_trust_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                state=ConduitState.STATE_3_2_GREETER_TRUST,
                payload=b"",
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_3b_greeter_signify_trust_serializer.rep_dump({"status": "ok"})

    @api("invite_3a_claimer_signify_trust", handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_3a_claimer_signify_trust(self, client_ctx, msg):
        msg = invite_3a_claimer_signify_trust_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_3_1_CLAIMER_TRUST,
                payload=b"",
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_3a_claimer_signify_trust_serializer.rep_dump({"status": "ok"})

    @api("invite_4_greeter_communicate", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_4_greeter_communicate(self, client_ctx, msg):
        msg = invite_4_greeter_communicate_serializer.req_load(msg)

        try:
            answer_payload = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=client_ctx.user_id,
                token=msg["token"],
                state=ConduitState.STATE_4_COMMUNICATE,
                payload=msg["payload"],
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_4_greeter_communicate_serializer.rep_dump(
            {"status": "ok", "payload": answer_payload}
        )

    @api("invite_4_claimer_communicate", handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_4_claimer_communicate(self, client_ctx, msg):
        msg = invite_4_claimer_communicate_serializer.req_load(msg)

        try:
            answer_payload = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_4_COMMUNICATE,
                payload=msg["payload"],
            )

        except InvitationNotFoundError:
            return {"status": "not_found"}

        except InvitationAlreadyDeletedError:
            return {"status": "already_deleted"}

        except InvitationInvalidStateError:
            return {"status": "invalid_state"}

        return invite_4_claimer_communicate_serializer.rep_dump(
            {"status": "ok", "payload": answer_payload}
        )

    async def conduit_exchange(
        self,
        organization_id: OrganizationID,
        greeter: Optional[UserID],
        token: UUID,
        state: ConduitState,
        payload: bytes,
    ) -> bytes:
        # Conduit exchange is done in two steps:
        # First we "talk" by providing our payload and retrieve the peer's
        # payload if he has talked prior to us.
        # Then we "listen" by waiting for the peer to provide his payload if we
        # have talk first, or to confirm us it has received our payload if we
        # have talk after him.
        filter_organization_id = organization_id
        filter_token = token

        def _conduit_updated_filter(event: str, organization_id: OrganizationID, token: UUID):
            return organization_id == filter_organization_id and token == filter_token

        with self._event_bus.waiter_on(
            BackendEvent.INVITE_CONDUIT_UPDATED, filter=_conduit_updated_filter
        ) as waiter:
            listen_ctx = await self._conduit_talk(organization_id, greeter, token, state, payload)

            while True:
                await waiter.wait()
                waiter.clear()
                peer_payload = await self._conduit_listen(listen_ctx)
                if peer_payload is not None:
                    return peer_payload

    async def _conduit_talk(
        self,
        organization_id: OrganizationID,
        greeter: Optional[UserID],  # None for claimer
        token: UUID,
        state: ConduitState,
        payload: bytes,
    ) -> ConduitListenCtx:
        """
        Raises:
            InvitationNotFoundError
            InvitationAlreadyDeletedError
            InvitationInvalidStateError
        """
        raise NotImplementedError()

    async def _conduit_listen(self, ctx: ConduitListenCtx) -> Optional[bytes]:
        """
        Returns ``None`` is listen is still needed
        Raises:
            InvitationNotFoundError
            InvitationAlreadyDeletedError
            InvitationInvalidStateError
        """
        raise NotImplementedError()

    async def new_for_user(
        self,
        organization_id: OrganizationID,
        greeter_user_id: UserID,
        claimer_email: str,
        created_on: Optional[Pendulum] = None,
    ) -> UserInvitation:
        """
        Raise: Nothing
        """
        raise NotImplementedError()

    async def new_for_device(
        self,
        organization_id: OrganizationID,
        greeter_user_id: UserID,
        created_on: Optional[Pendulum] = None,
    ) -> DeviceInvitation:
        """
        Raise: Nothing
        """
        raise NotImplementedError()

    async def delete(
        self,
        organization_id: OrganizationID,
        greeter: UserID,
        token: UUID,
        on: Pendulum,
        reason: InvitationDeletedReason,
    ) -> None:
        """
        Raises:
            InvitationNotFoundError
            InvitationAlreadyDeletedError
        """
        raise NotImplementedError()

    async def list(self, organization_id: OrganizationID, greeter: UserID) -> List[Invitation]:
        """
        Raises: Nothing
        """
        raise NotImplementedError()

    async def info(self, organization_id: OrganizationID, token: UUID) -> Invitation:
        """
        Raises:
            InvitationNotFoundError
            InvitationAlreadyDeletedError
        """
        raise NotImplementedError()

    async def claimer_joined(
        self, organization_id: OrganizationID, greeter: UserID, token: UUID
    ) -> None:
        """
        Raises: Nothing
        """
        raise NotImplementedError()

    async def claimer_left(
        self, organization_id: OrganizationID, greeter: UserID, token: UUID
    ) -> None:
        """
        Raises: Nothing
        """
        raise NotImplementedError()
