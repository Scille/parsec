# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from parsec.backend.backend_events import BackendEvent
import attr
import trio
import smtplib
import ssl
import sys
import tempfile
from enum import Enum
from uuid import UUID, uuid4
from collections import defaultdict
from typing import Dict, List, Optional, Union, Set, cast
from pendulum import DateTime, now as pendulum_now
from email.message import Message
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from structlog import get_logger

from parsec.crypto import PublicKey
from parsec.event_bus import EventBus, EventCallback, EventFilterCallback
from parsec.api.data import UserProfile
from parsec.api.protocol import (
    OrganizationID,
    UserID,
    HumanHandle,
    HandshakeType,
    InvitationType,
    InvitationDeletedReason,
    InvitationStatus,
    invite_new_serializer,
    invite_delete_serializer,
    invite_list_serializer,
    invite_info_serializer,
    invite_1_claimer_wait_peer_serializer,
    invite_1_greeter_wait_peer_serializer,
    invite_2a_claimer_send_hashed_nonce_serializer,
    invite_2a_greeter_get_hashed_nonce_serializer,
    invite_2b_greeter_send_nonce_serializer,
    invite_2b_claimer_send_nonce_serializer,
    invite_3a_greeter_wait_peer_trust_serializer,
    invite_3a_claimer_signify_trust_serializer,
    invite_3b_claimer_wait_peer_trust_serializer,
    invite_3b_greeter_signify_trust_serializer,
    invite_4_greeter_communicate_serializer,
    invite_4_claimer_communicate_serializer,
)
from parsec.backend.templates import get_template
from parsec.backend.utils import catch_protocol_errors, api
from parsec.backend.config import BackendConfig, EmailConfig, SmtpEmailConfig, MockedEmailConfig
from parsec.core.types import BackendInvitationAddr


logger = get_logger()


class CloseInviteConnection(Exception):
    pass


class InvitationError(Exception):
    pass


class InvitationNotFoundError(InvitationError):
    pass


class InvitationAlreadyDeletedError(InvitationError):
    pass


class InvitationInvalidStateError(InvitationError):
    pass


class InvitationAlreadyMemberError(InvitationError):
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
    TYPE = InvitationType.USER
    greeter_user_id: UserID
    greeter_human_handle: Optional[HumanHandle]
    claimer_email: str
    token: UUID = attr.ib(factory=uuid4)
    created_on: DateTime = attr.ib(factory=pendulum_now)
    status: InvitationStatus = InvitationStatus.IDLE

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceInvitation:
    TYPE = InvitationType.DEVICE
    greeter_user_id: UserID
    greeter_human_handle: Optional[HumanHandle]
    token: UUID = attr.ib(factory=uuid4)
    created_on: DateTime = attr.ib(factory=pendulum_now)
    status: InvitationStatus = InvitationStatus.IDLE

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)


Invitation = Union[UserInvitation, DeviceInvitation]


def generate_invite_email(
    from_addr: str,
    to_addr: str,
    reply_to: Optional[str],
    greeter_name: Optional[str],  # Noe for device invitation
    organization_id: OrganizationID,
    invitation_url: str,
) -> Message:
    html = get_template("invitation_mail.html").render(
        greeter=greeter_name, organization_id=organization_id, invitation_url=invitation_url
    )
    text = get_template("invitation_mail.txt").render(
        greeter=greeter_name, organization_id=organization_id, invitation_url=invitation_url
    )

    # mail settings
    message = MIMEMultipart("alternative")
    if greeter_name:
        message["Subject"] = f"[Parsec] { greeter_name } invited you to { organization_id }"
    else:
        message["Subject"] = f"[Parsec] New device invitation to { organization_id }"
    message["From"] = from_addr
    message["To"] = to_addr
    if reply_to:
        message["Reply-To"] = reply_to

    # Turn parts into MIMEText objects
    part1 = MIMEText(text, "plain")
    part2 = MIMEText(html, "html")

    # Add HTML/plain-text parts to MIMEMultipart message
    # The email client will try to render the last part first
    message.attach(part1)
    message.attach(part2)

    return message


async def _smtp_send_mail(email_config: SmtpEmailConfig, to_addr: str, message: Message) -> None:
    def _do():
        try:
            context = ssl.create_default_context()
            if email_config.use_ssl:
                server = smtplib.SMTP_SSL(email_config.host, email_config.port, context=context)
            else:
                server = smtplib.SMTP(email_config.host, email_config.port)

            with server:
                if email_config.use_tls and not email_config.use_ssl:
                    if server.starttls(context=context)[0] != 220:
                        logger.warning("Email TLS connexion isn't encrypted")
                if email_config.host_user and email_config.host_password:
                    server.login(email_config.host_user, email_config.host_password)
                server.sendmail(email_config.sender, to_addr, message.as_string())

        except smtplib.SMTPException as e:
            logger.warning("SMTP error", exc_info=e, to_addr=to_addr, subject=message["Subject"])

    await trio.to_thread.run_sync(_do)


async def _mocked_send_mail(
    email_config: MockedEmailConfig, to_addr: str, message: Message
) -> None:
    def _do():
        tmpfile_fd, tmpfile_path = tempfile.mkstemp(
            prefix="tmp-email-", suffix=".html", dir=email_config.tmpdir
        )
        tmpfile = open(tmpfile_path, "w")
        tmpfile.write(message.as_string())
        print(
            f"""\
A request to send an e-mail to {to_addr} has been triggered and mocked.
The mail file can be found here: {tmpfile.name}\n""",
            tmpfile.name,
            file=sys.stderr,
        )

    await trio.to_thread.run_sync(_do)


async def send_email(email_config: EmailConfig, to_addr: str, message: Message) -> None:
    if isinstance(email_config, SmtpEmailConfig):
        await _smtp_send_mail(email_config, to_addr, message)
    else:
        await _mocked_send_mail(email_config, to_addr, message)


class BaseInviteComponent:
    def __init__(self, event_bus: EventBus, config: BackendConfig):
        self._event_bus = event_bus
        self._config = config
        # We use the `invite.status_changed` event to keep a list of all the
        # invitation claimers connected accross all backends.
        #
        # This is useful to display the invitations ready to be greeted.
        # Note we rely on a per-backend list in memory instead of storing this
        # information in database so that we default to no claimer present
        # (which is the most likely when a backend is restarted) .
        #
        # However there are multiple ways this list can go out of sync:
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

        def _on_status_changed(event: Enum, organization_id, greeter, token, status) -> None:
            if status == InvitationStatus.READY:
                self._claimers_ready[organization_id].add(token)
            else:  # Invitation deleted or back to idle
                self._claimers_ready[organization_id].discard(token)

        self._event_bus.connect(
            BackendEvent.INVITE_STATUS_CHANGED, cast(EventCallback, _on_status_changed)
        )

    @api("invite_new", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_invite_new(self, client_ctx, msg):
        msg = invite_new_serializer.req_load(msg)

        def _to_http_redirection_url(client_ctx, invitation):
            return BackendInvitationAddr.build(
                backend_addr=self._config.backend_addr,
                organization_id=client_ctx.organization_id,
                invitation_type=invitation.TYPE,
                token=invitation.token,
            ).to_http_redirection_url()

        if msg["type"] == InvitationType.USER:
            if client_ctx.profile != UserProfile.ADMIN:
                return invite_new_serializer.rep_dump({"status": "not_allowed"})
            try:
                invitation = await self.new_for_user(
                    organization_id=client_ctx.organization_id,
                    greeter_user_id=client_ctx.user_id,
                    claimer_email=msg["claimer_email"],
                )
            except InvitationAlreadyMemberError:
                return invite_new_serializer.rep_dump({"status": "already_member"})

            if msg["send_email"]:
                if client_ctx.human_handle:
                    greeter_name = client_ctx.human_handle.label
                    reply_to = f"{client_ctx.human_handle.label} <{client_ctx.human_handle.email}>"
                else:
                    greeter_name = str(client_ctx.user_id)
                    reply_to = None
                message = generate_invite_email(
                    from_addr=self._config.email_config.sender,
                    to_addr=invitation.claimer_email,
                    greeter_name=greeter_name,
                    reply_to=reply_to,
                    organization_id=client_ctx.organization_id,
                    invitation_url=_to_http_redirection_url(client_ctx, invitation),
                )
                await send_email(
                    email_config=self._config.email_config,
                    to_addr=invitation.claimer_email,
                    message=message,
                )

        else:  # Device
            if msg["send_email"] and not client_ctx.human_handle:
                return invite_new_serializer.rep_dump({"status": "not_available"})

            invitation = await self.new_for_device(
                organization_id=client_ctx.organization_id, greeter_user_id=client_ctx.user_id
            )

            if msg["send_email"]:
                message = generate_invite_email(
                    from_addr=self._config.email_config.sender,
                    to_addr=client_ctx.human_handle.email,
                    greeter_name=None,
                    reply_to=None,
                    organization_id=client_ctx.organization_id,
                    invitation_url=_to_http_redirection_url(client_ctx, invitation),
                )
                await send_email(
                    email_config=self._config.email_config,
                    to_addr=client_ctx.human_handle.email,
                    message=message,
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

    @api("invite_1_claimer_wait_peer", long_request=True, handshake_types=[HandshakeType.INVITED])
    @catch_protocol_errors
    async def api_invite_1_claimer_wait_peer(self, client_ctx, msg):
        """
        Raises:
            CloseInviteConnection
        """
        msg = invite_1_claimer_wait_peer_serializer.req_load(msg)
        try:
            greeter_public_key = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_1_WAIT_PEERS,
                payload=msg["claimer_public_key"].encode(),
            )

        except InvitationAlreadyDeletedError as exc:
            # Notify parent that the connection shall be close because the invitation token is no longer valid.
            raise CloseInviteConnection from exc

        except InvitationNotFoundError:
            return {"status": "not_found"}

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
        """
        Raises:
            CloseInviteConnection
        """
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

        except InvitationAlreadyDeletedError as exc:
            # Notify parent that the connection shall be close because the invitation token is no longer valid.
            raise CloseInviteConnection from exc

        except InvitationNotFoundError:
            return {"status": "not_found"}

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
        """
        Raises:
            CloseInviteConnection
        """
        msg = invite_2b_claimer_send_nonce_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_2_3_CLAIMER_NONCE,
                payload=msg["claimer_nonce"],
            )

        except InvitationAlreadyDeletedError as exc:
            # Notify parent that the connection shall be close because the invitation token is no longer valid.
            raise CloseInviteConnection from exc

        except InvitationNotFoundError:
            return {"status": "not_found"}

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
        """
        Raises:
            CloseInviteConnection
        """
        msg = invite_3b_claimer_wait_peer_trust_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_3_2_GREETER_TRUST,
                payload=b"",
            )

        except InvitationAlreadyDeletedError as exc:
            # Notify parent that the connection shall be close because the invitation token is no longer valid.
            raise CloseInviteConnection from exc

        except InvitationNotFoundError:
            return {"status": "not_found"}

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
        """
        Raises:
            CloseInviteConnection
        """
        msg = invite_3a_claimer_signify_trust_serializer.req_load(msg)

        try:
            await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_3_1_CLAIMER_TRUST,
                payload=b"",
            )

        except InvitationAlreadyDeletedError as exc:
            # Notify parent that the connection shall be close because the invitation token is no longer valid.
            raise CloseInviteConnection from exc

        except InvitationNotFoundError:
            return {"status": "not_found"}

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
        """
        Raises:
            CloseInviteConnection
        """
        msg = invite_4_claimer_communicate_serializer.req_load(msg)

        try:
            answer_payload = await self.conduit_exchange(
                organization_id=client_ctx.organization_id,
                greeter=None,
                token=client_ctx.invitation.token,
                state=ConduitState.STATE_4_COMMUNICATE,
                payload=msg["payload"],
            )

        except InvitationAlreadyDeletedError as exc:
            # Notify parent that the connection shall be close because the invitation token is no longer valid.
            raise CloseInviteConnection from exc

        except InvitationNotFoundError:
            return {"status": "not_found"}

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
        # have talked first, or to confirm us it has received our payload if we
        # have talked after him.
        filter_organization_id = organization_id
        filter_token = token

        def _conduit_updated_filter(event: Enum, organization_id: OrganizationID, token: UUID):
            return organization_id == filter_organization_id and token == filter_token

        with self._event_bus.waiter_on(
            BackendEvent.INVITE_CONDUIT_UPDATED,
            filter=cast(EventFilterCallback, _conduit_updated_filter),
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
        created_on: Optional[DateTime] = None,
    ) -> UserInvitation:
        """
        Raise: Nothing
        """
        raise NotImplementedError()

    async def new_for_device(
        self,
        organization_id: OrganizationID,
        greeter_user_id: UserID,
        created_on: Optional[DateTime] = None,
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
        on: DateTime,
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
