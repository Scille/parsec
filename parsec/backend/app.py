# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional
import trio
import attr
from structlog import get_logger
from logging import DEBUG as LOG_LEVEL_DEBUG
from pendulum import now as pendulum_now
from async_generator import asynccontextmanager

from parsec.event_bus import EventBus
from parsec.logging import get_log_level
from parsec.api.transport import TransportError, TransportClosedByPeer, Transport
from parsec.api.protocol import (
    packb,
    unpackb,
    ProtocolError,
    MessageSerializationError,
    InvalidMessageError,
    ServerHandshake,
)
from parsec.backend.utils import check_anonymous_api_allowed, CancelledByNewRequest
from parsec.backend.config import BackendConfig
from parsec.backend.memory import components_factory as mocked_components_factory
from parsec.backend.postgresql import components_factory as postgresql_components_factory
from parsec.backend.user import UserNotFoundError
from parsec.backend.organization import OrganizationNotFoundError


logger = get_logger()


@attr.s(slots=True, repr=False)
class LoggedClientContext:
    transport = attr.ib()
    organization_id = attr.ib()
    is_admin = attr.ib()
    device_id = attr.ib()
    public_key = attr.ib()
    verify_key = attr.ib()
    event_bus_ctx = attr.ib(default=None)
    conn_id = attr.ib(init=False)
    logger = attr.ib(init=False)
    channels = attr.ib(factory=lambda: trio.open_memory_channel(100))
    realms = attr.ib(factory=set)

    def __repr__(self):
        return (
            f"LoggedClientContext(conn={self.conn_id}, "
            f"org={self.organization_id}, "
            f"device={self.device_id})"
        )

    def __attrs_post_init__(self):
        self.conn_id = self.transport.conn_id
        self.logger = self.transport.logger = self.transport.logger.bind(
            organization_id=str(self.organization_id), client_id=str(self.device_id)
        )

    @property
    def user_id(self):
        return self.device_id.user_id

    @property
    def device_name(self):
        return self.device_id.device_name

    @property
    def send_events_channel(self):
        send_channel, _ = self.channels
        return send_channel

    @property
    def receive_events_channel(self):
        _, receive_channel = self.channels
        return receive_channel


@attr.s(slots=True, repr=False)
class AnonymousClientContext:
    transport = attr.ib()
    organization_id = attr.ib()
    conn_id = attr.ib(init=False)
    logger = attr.ib(init=False)
    device_id = None

    def __repr__(self):
        return f"AnonymousClientContext(conn={self.conn_id}, org={self.organization_id})"

    def __attrs_post_init__(self):
        self.conn_id = self.transport.conn_id
        self.logger = self.transport.logger = self.transport.logger.bind(
            organization_id=str(self.organization_id), client_id="<anonymous>"
        )


@attr.s
class AdministrationClientContext:
    transport = attr.ib()
    conn_id = attr.ib(init=False)
    logger = attr.ib(init=False)
    device_id = None

    def __attrs_post_init__(self):
        self.conn_id = self.transport.conn_id
        self.logger = self.transport.logger = self.transport.logger.bind(
            client_id="<administration>"
        )


def _filter_binary_fields(data):
    return {k: v if not isinstance(v, bytes) else b"[...]" for k, v in data.items()}


@asynccontextmanager
async def backend_app_factory(config: BackendConfig, event_bus: Optional[EventBus] = None):
    event_bus = event_bus or EventBus()

    if config.db_url == "MOCKED":
        components_factory = mocked_components_factory
    else:
        components_factory = postgresql_components_factory

    async with components_factory(config=config, event_bus=event_bus) as components:
        yield BackendApp(
            config=config,
            event_bus=event_bus,
            user=components["user"],
            organization=components["organization"],
            message=components["message"],
            realm=components["realm"],
            vlob=components["vlob"],
            ping=components["ping"],
            blockstore=components["blockstore"],
            block=components["block"],
            events=components["events"],
        )


class BackendApp:
    def __init__(
        self,
        config,
        event_bus,
        user,
        organization,
        message,
        realm,
        vlob,
        ping,
        blockstore,
        block,
        events,
    ):
        self.config = config
        self.event_bus = event_bus
        self.user = user
        self.organization = organization
        self.message = message
        self.realm = realm
        self.vlob = vlob
        self.ping = ping
        self.blockstore = blockstore
        self.block = block
        self.events = events

        self.logged_cmds = {
            "events_subscribe": self.events.api_events_subscribe,
            "events_listen": self.events.api_events_listen,
            "ping": self.ping.api_ping,
            # Message
            "message_get": self.message.api_message_get,
            # User&Device
            "user_get": self.user.api_user_get,
            "user_find": self.user.api_user_find,
            "user_invite": self.user.api_user_invite,
            "user_cancel_invitation": self.user.api_user_cancel_invitation,
            "user_create": self.user.api_user_create,
            "user_revoke": self.user.api_user_revoke,
            "device_invite": self.user.api_device_invite,
            "device_cancel_invitation": self.user.api_device_cancel_invitation,
            "device_create": self.user.api_device_create,
            # Block
            "block_create": self.block.api_block_create,
            "block_read": self.block.api_block_read,
            # Vlob
            "vlob_poll_changes": self.vlob.api_vlob_poll_changes,
            "vlob_create": self.vlob.api_vlob_create,
            "vlob_read": self.vlob.api_vlob_read,
            "vlob_update": self.vlob.api_vlob_update,
            "vlob_list_versions": self.vlob.api_vlob_list_versions,
            "vlob_maintenance_get_garbage_collection_batch": self.vlob.api_vlob_maintenance_get_garbage_collection_batch,
            "vlob_maintenance_save_garbage_collection_batch": self.vlob.api_vlob_maintenance_save_garbage_collection_batch,
            "vlob_maintenance_get_reencryption_batch": self.vlob.api_vlob_maintenance_get_reencryption_batch,
            "vlob_maintenance_save_reencryption_batch": self.vlob.api_vlob_maintenance_save_reencryption_batch,
            # Realm
            "realm_create": self.realm.api_realm_create,
            "realm_status": self.realm.api_realm_status,
            "realm_get_role_certificates": self.realm.api_realm_get_role_certificates,
            "realm_update_roles": self.realm.api_realm_update_roles,
            "realm_start_garbage_collection_maintenance": self.realm.api_realm_start_garbage_collection_maintenance,
            "realm_finish_garbage_collection_maintenance": self.realm.api_realm_finish_garbage_collection_maintenance,
            "realm_start_reencryption_maintenance": self.realm.api_realm_start_reencryption_maintenance,
            "realm_finish_reencryption_maintenance": self.realm.api_realm_finish_reencryption_maintenance,
        }
        self.anonymous_cmds = {
            "user_claim": self.user.api_user_claim,
            "user_get_invitation_creator": self.user.api_user_get_invitation_creator,
            "device_claim": self.user.api_device_claim,
            "device_get_invitation_creator": self.user.api_device_get_invitation_creator,
            "organization_bootstrap": self.organization.api_organization_bootstrap,
            "ping": self.ping.api_ping,
        }
        self.administration_cmds = {
            "organization_create": self.organization.api_organization_create,
            "organization_stats": self.organization.api_organization_stats,
            "organization_status": self.organization.api_organization_status,
            "ping": self.ping.api_ping,
        }
        for fn in self.anonymous_cmds.values():
            check_anonymous_api_allowed(fn)

    async def _do_handshake(self, transport):
        context = None
        error_infos = None
        try:
            handshake = transport.handshake = ServerHandshake()
            challenge_req = handshake.build_challenge_req()
            await transport.send(challenge_req)
            answer_req = await transport.recv()

            handshake.process_answer_req(answer_req)

            if handshake.answer_type == "authenticated":
                organization_id = handshake.answer_data["organization_id"]
                device_id = handshake.answer_data["device_id"]
                expected_rvk = handshake.answer_data["rvk"]
                try:
                    organization = await self.organization.get(organization_id)
                    user, device = await self.user.get_user_with_device(organization_id, device_id)

                except (OrganizationNotFoundError, UserNotFoundError, KeyError) as exc:
                    result_req = handshake.build_bad_identity_result_req()
                    error_infos = {
                        "reason": str(exc),
                        "handshake_type": "authenticated",
                        "organization_id": organization_id,
                        "device_id": device_id,
                    }

                else:
                    if organization.root_verify_key != expected_rvk:
                        result_req = handshake.build_rvk_mismatch_result_req()
                        error_infos = {
                            "reason": "Bad root verify key",
                            "handshake_type": "authenticated",
                            "organization_id": organization_id,
                            "device_id": device_id,
                        }

                    elif (
                        organization.expiration_date is not None
                        and organization.expiration_date <= pendulum_now()
                    ):
                        result_req = handshake.build_organization_expired_result_req()
                        error_infos = {
                            "reason": "Expired organization",
                            "handshake_type": "authenticated",
                            "organization_id": organization_id,
                            "device_id": device_id,
                        }

                    elif user.revoked_on and user.revoked_on <= pendulum_now():
                        result_req = handshake.build_revoked_device_result_req()
                        error_infos = {
                            "reason": "Revoked device",
                            "handshake_type": "authenticated",
                            "organization_id": organization_id,
                            "device_id": device_id,
                        }

                    else:
                        context = LoggedClientContext(
                            transport,
                            organization_id,
                            user.is_admin,
                            device_id,
                            user.public_key,
                            device.verify_key,
                        )
                        result_req = handshake.build_result_req(device.verify_key)

            elif handshake.answer_type == "anonymous":
                organization_id = handshake.answer_data["organization_id"]
                expected_rvk = handshake.answer_data["rvk"]
                try:
                    organization = await self.organization.get(organization_id)

                except OrganizationNotFoundError:
                    result_req = handshake.build_bad_identity_result_req()
                    error_infos = {
                        "reason": "Bad organization",
                        "handshake_type": "anonymous",
                        "organization_id": organization_id,
                    }

                else:
                    if expected_rvk and organization.root_verify_key != expected_rvk:
                        result_req = handshake.build_rvk_mismatch_result_req()
                        error_infos = {
                            "reason": "Bad root verify key",
                            "handshake_type": "anonymous",
                            "organization_id": organization_id,
                        }

                    elif (
                        organization.expiration_date is not None
                        and organization.expiration_date <= pendulum_now()
                    ):
                        result_req = handshake.build_organization_expired_result_req()
                        error_infos = {
                            "reason": "Expired organization",
                            "handshake_type": "anonymous",
                            "organization_id": organization_id,
                        }

                    else:
                        context = AnonymousClientContext(transport, organization_id)
                        result_req = handshake.build_result_req()

            elif handshake.answer_type == "administration":
                if handshake.answer_data["token"] == self.config.administration_token:
                    context = AdministrationClientContext(transport)
                    result_req = handshake.build_result_req()
                else:
                    result_req = handshake.build_bad_administration_token_result_req()
                    error_infos = {"reason": "Bad token", "handshake_type": "administration"}

            else:
                assert False

        except ProtocolError as exc:
            result_req = handshake.build_bad_protocol_result_req(str(exc))
            error_infos = {"reason": str(exc), "handshake_type": handshake.answer_type}

        await transport.send(result_req)
        return context, error_infos

    async def handle_client(self, stream):
        selected_logger = logger

        try:
            transport = await Transport.init_for_server(stream)

        except TransportClosedByPeer as exc:
            selected_logger.info("Connection dropped: client has left", reason=str(exc))
            return

        except TransportError as exc:
            # A crash during transport setup could mean the client tried to
            # access us from a web browser (hence sending http request).

            content_body = b"This service requires use of the WebSocket protocol"
            content = (
                b"HTTP/1.1 426 OK\r\n"
                b"Upgrade: WebSocket\r\n"
                b"Content-Length: %d\r\n"
                b"Connection: Upgrade\r\n"
                b"Content-Type: text/html; charset=UTF-8\r\n"
                b"\r\n"
            ) % len(content_body)

            try:
                await stream.send_all(content + content_body)
                await stream.aclose()

            except trio.BrokenResourceError:
                # Stream is really dead, nothing else to do...
                pass

            selected_logger.info("Connection dropped: websocket error", reason=str(exc))
            return

        selected_logger = transport.logger

        try:
            client_ctx, error_infos = await self._do_handshake(transport)
            if not client_ctx:
                # Invalid handshake
                await stream.aclose()
                selected_logger.info("Connection dropped: bad handshake", **error_infos)
                return

            selected_logger = client_ctx.logger
            selected_logger.info("Connection established")

            if hasattr(client_ctx, "event_bus_ctx"):
                with self.event_bus.connection_context() as client_ctx.event_bus_ctx:
                    with trio.CancelScope() as cancel_scope:

                        def _on_revoked(event, organization_id, user_id):
                            if (
                                organization_id == client_ctx.organization_id
                                and user_id == client_ctx.user_id
                            ):
                                cancel_scope.cancel()

                        client_ctx.event_bus_ctx.connect("user.revoked", _on_revoked)
                        await self._handle_client_loop(transport, client_ctx)

            else:
                await self._handle_client_loop(transport, client_ctx)

            await transport.aclose()

        except TransportClosedByPeer as exc:
            selected_logger.info("Connection dropped: client has left", reason=str(exc))

        except (TransportError, MessageSerializationError) as exc:
            rep = {"status": "invalid_msg_format", "reason": "Invalid message format"}
            try:
                await transport.send(packb(rep))
            except TransportError:
                pass
            await transport.aclose()
            selected_logger.info("Connection dropped: invalid data", reason=str(exc))

    async def _handle_client_loop(self, transport, client_ctx):
        raw_req = None
        while True:
            # raw_req can be already defined if we received a new request
            # while processing a command
            raw_req = raw_req or await transport.recv()
            req = unpackb(raw_req)
            if get_log_level() <= LOG_LEVEL_DEBUG:
                client_ctx.logger.debug("Request", req=_filter_binary_fields(req))
            try:
                cmd = req.get("cmd", "<missing>")
                if not isinstance(cmd, str):
                    raise KeyError()

                if isinstance(client_ctx, AdministrationClientContext):
                    cmd_func = self.administration_cmds[cmd]

                elif isinstance(client_ctx, LoggedClientContext):
                    cmd_func = self.logged_cmds[cmd]

                else:
                    cmd_func = self.anonymous_cmds[cmd]

            except KeyError:
                rep = {"status": "unknown_command", "reason": "Unknown command"}

            else:
                try:
                    rep = await cmd_func(client_ctx, req)

                except InvalidMessageError as exc:
                    rep = {
                        "status": "bad_message",
                        "errors": exc.errors,
                        "reason": "Invalid message.",
                    }

                except ProtocolError as exc:
                    rep = {"status": "bad_message", "reason": str(exc)}

                except CancelledByNewRequest as exc:
                    # Long command handling such as message_get can be cancelled
                    # when the peer send a new request
                    raw_req = exc.new_raw_req
                    continue

            if get_log_level() <= LOG_LEVEL_DEBUG:
                client_ctx.logger.debug("Response", rep=_filter_binary_fields(req))
            else:
                client_ctx.logger.info("Request", cmd=cmd, status=rep["status"])
            raw_rep = packb(rep)
            await transport.send(raw_rep)
            raw_req = None
