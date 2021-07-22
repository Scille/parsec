# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from typing import Dict, Optional
import trio
from trio.abc import Stream
from structlog import get_logger
from logging import DEBUG as LOG_LEVEL_DEBUG
from async_generator import asynccontextmanager
from wsgiref.handlers import format_date_time
from http import HTTPStatus
import h11

from parsec._version import __version__ as parsec_version
from parsec.backend.backend_events import BackendEvent
from parsec.event_bus import EventBus
from parsec.logging import get_log_level
from parsec.api.transport import TransportError, TransportClosedByPeer, Transport, TRANSPORT_TARGET
from parsec.api.protocol import (
    packb,
    unpackb,
    ProtocolError,
    MessageSerializationError,
    InvalidMessageError,
    InvitationStatus,
)
from parsec.backend.utils import CancelledByNewRequest, collect_apis
from parsec.backend.config import BackendConfig
from parsec.backend.client_context import AuthenticatedClientContext, InvitedClientContext
from parsec.backend.handshake import do_handshake
from parsec.backend.memory import components_factory as mocked_components_factory
from parsec.backend.postgresql import components_factory as postgresql_components_factory
from parsec.backend.http import HTTPRequest
from parsec.backend.invite import CloseInviteConnection


logger = get_logger()


# Max size for the very first HTTP request (request line + headers, so excluding body)
# that arrives on a socket. This request should either upgrade to websocket
# (from where on `parsec.api.transport.Transport` handles the socket) or be a
# single HTTP request that we are going to respond to before closing the
# connection (i.e. we ignore keep-alive)
# The choice of 8Ko is more or less arbitrary but it range with what most web servers do.
MAX_INITIAL_HTTP_REQUEST_SIZE = 8 * 1024


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
            webhooks=components["webhooks"],
            http=components["http"],
            user=components["user"],
            invite=components["invite"],
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
        webhooks,
        http,
        user,
        invite,
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

        self.webhooks = webhooks
        self.http = http
        self.user = user
        self.invite = invite
        self.organization = organization
        self.message = message
        self.realm = realm
        self.vlob = vlob
        self.ping = ping
        self.blockstore = blockstore
        self.block = block
        self.events = events

        self.apis = collect_apis(
            user, invite, organization, message, realm, vlob, ping, blockstore, block, events
        )

        if self.config.debug:
            self.server_header = f"parsec/{parsec_version} {h11.PRODUCT_ID}".encode("ascii")
        else:
            self.server_header = b"parsec"

    async def handle_client(self, stream):
        # Uses max_size - 1 given h11 enforces the check only if it current
        # internal buffer doesn't contain an entire message.
        # Note that given we fetch by batches of MAX_INITIAL_HTTP_REQUEST_SIZE,
        # we can end up with a final message as big as 2 * MAX_INITIAL_HTTP_REQUEST_SIZE - 1
        # if the client starts by sending a MAX_INITIAL_HTTP_REQUEST_SIZE - 1
        # tcp trame, then another MAX_INITIAL_HTTP_REQUEST_SIZE.
        conn = h11.Connection(
            h11.SERVER, max_incomplete_event_size=MAX_INITIAL_HTTP_REQUEST_SIZE - 1
        )
        try:
            # Fetch the initial request
            while True:
                try:
                    data = await stream.receive_some(MAX_INITIAL_HTTP_REQUEST_SIZE)
                except trio.BrokenResourceError:
                    # The socket got broken in an unexpected way (the peer has most
                    # likely left without telling us, or has reseted the connection)
                    return

                conn.receive_data(data)
                event = conn.next_event()

                if event is h11.NEED_DATA:
                    continue
                if isinstance(event, h11.Request):
                    break
                if isinstance(event, h11.ConnectionClosed):
                    # Peer has left
                    return
                else:
                    logger.error("Unexpected event", client_event=event)
                    return

            # See https://h11.readthedocs.io/en/v0.10.0/api.html#flow-control
            if conn.they_are_waiting_for_100_continue:
                await stream.send_all(
                    conn.send(h11.InformationalResponse(status_code=100, headers=[]))
                )

            def _get_header(key: bytes) -> Optional[bytes]:
                # h11 guarantees the headers key are always lowercase
                return next((v for k, v in event.headers if k == key), None)

            # Do https redirection if incoming request doesn't follow forward proto rules
            if self.config.forward_proto_enforce_https:
                header_key, header_expected_value = self.config.forward_proto_enforce_https
                header_value = _get_header(header_key)
                # If redirection header match and protocol match, then no need for a redirection.
                if header_value is not None and header_value != header_expected_value:
                    location_url = (
                        b"https://" + self.config.backend_addr.netloc.encode("ascii") + event.target
                    )
                    await self._send_http_reply(
                        stream=stream,
                        conn=conn,
                        status_code=301,
                        headers={b"location": location_url},
                    )
                    return await stream.aclose()

            # Test for websocket upgrade considering:
            # - Upgrade header has been introduced in HTTP 1.1 RFC
            # - Connection&Upgrade fields are case-insensitive according to RFC
            # - Only `/ws` target are valid for upgrade, this allow us to reserve
            #   other target for future use
            # - We fallback to HTTP in case of invalid upgrade query for simplicity
            if (
                event.http_version == b"1.1"
                and event.target == TRANSPORT_TARGET.encode()
                and (_get_header(b"connection") or b"").lower() == b"upgrade"
                and (_get_header(b"upgrade") or b"").lower() == b"websocket"
            ):
                await self._handle_client_websocket(stream, event)
            else:
                await self._handle_client_http(stream, conn, event)

        except h11.RemoteProtocolError as exc:
            # Peer is drunk, tell him and leave...
            await self._send_http_reply(stream, conn, status_code=exc.error_status_hint)

        finally:
            # Note the stream might already be closed (e.g. through `Transport.aclose`)
            # but it's ok given this operation is idempotent
            await stream.aclose()

    async def _send_http_reply(
        self,
        stream: Stream,
        conn: h11.Connection,
        status_code: int,
        headers: Dict[bytes, bytes] = {},
        data: Optional[bytes] = None,
    ) -> None:
        reason = HTTPStatus(status_code).phrase
        headers = list(
            {
                **headers,
                # Add default headers
                b"server": self.server_header,
                b"date": format_date_time(None).encode("ascii"),
                b"content-Length": str(len(data or b"")).encode("ascii"),
                # Inform we don't support keep-alive (h11 will know what to do from there)
                b"connection": b"close",
            }.items()
        )
        try:
            await stream.send_all(
                conn.send(h11.Response(status_code=status_code, headers=headers, reason=reason))
            )
            if data:
                await stream.send_all(conn.send(h11.Data(data=data)))
            await stream.send_all(conn.send(h11.EndOfMessage()))
        except trio.BrokenResourceError:
            # Given we don't support keep-alive, the connection is going to be
            # shutdown anyway, so we can safely ignore the fact peer has left
            pass

    async def _handle_client_http(self, stream, conn, request):
        # TODO: right now we handle a single request then close the connection
        # hence HTTP 1.1 keep-alive is not supported
        req = HTTPRequest.from_h11_req(request)
        rep = await self.http.handle_request(req)
        await self._send_http_reply(
            stream, conn, status_code=rep.status_code, headers=rep.headers, data=rep.data
        )

    async def _handle_client_websocket(self, stream, request):
        selected_logger = logger

        try:
            transport = await Transport.init_for_server(stream, upgrade_request=request)

        except TransportClosedByPeer as exc:
            selected_logger.info("Connection dropped: client has left", reason=str(exc))
            return

        except TransportError as exc:
            selected_logger.info("Connection dropped: websocket error", reason=str(exc))
            return

        selected_logger = transport.logger

        try:
            client_ctx, error_infos = await do_handshake(self, transport)
            if not client_ctx:
                # Invalid handshake
                # TODO Fragile test based on reason, make it more robust
                if error_infos and error_infos.get("reason", "") == "Expired organization":
                    organization_id = error_infos["organization_id"]
                    await self.events.send(
                        BackendEvent.ORGANIZATION_EXPIRED, organization_id=organization_id
                    )
                selected_logger.info("Connection dropped: bad handshake", **error_infos)
                return

            selected_logger = client_ctx.logger
            selected_logger.info("Connection established")

            if isinstance(client_ctx, AuthenticatedClientContext):
                with trio.CancelScope() as cancel_scope:
                    with self.event_bus.connection_context() as client_ctx.event_bus_ctx:

                        def _on_revoked(event, organization_id, user_id):
                            if (
                                organization_id == client_ctx.organization_id
                                and user_id == client_ctx.user_id
                            ):
                                cancel_scope.cancel()

                        def _on_expired(event, organization_id):
                            if organization_id == client_ctx.organization_id:
                                cancel_scope.cancel()

                        client_ctx.event_bus_ctx.connect(BackendEvent.USER_REVOKED, _on_revoked)
                        client_ctx.event_bus_ctx.connect(
                            BackendEvent.ORGANIZATION_EXPIRED, _on_expired
                        )
                        await self._handle_client_websocket_loop(transport, client_ctx)

            elif isinstance(client_ctx, InvitedClientContext):
                await self.invite.claimer_joined(
                    organization_id=client_ctx.organization_id,
                    greeter=client_ctx.invitation.greeter_user_id,
                    token=client_ctx.invitation.token,
                )
                try:
                    with trio.CancelScope() as cancel_scope:
                        with self.event_bus.connection_context() as event_bus_ctx:

                            def _on_invite_status_changed(
                                event, organization_id, greeter, token, status
                            ):
                                if (
                                    status == InvitationStatus.DELETED
                                    and organization_id == client_ctx.organization_id
                                    and token == client_ctx.invitation.token
                                ):
                                    cancel_scope.cancel()

                            event_bus_ctx.connect(
                                BackendEvent.INVITE_STATUS_CHANGED, _on_invite_status_changed
                            )
                            await self._handle_client_websocket_loop(transport, client_ctx)

                except CloseInviteConnection:
                    # If the invitation has been deleted after the invited handshake,
                    # invitation commands can raise an InvitationAlreadyDeletedError.
                    # This error is converted to a CloseInviteConnection
                    # The connection shall be closed due to a BackendEvent.INVITE_STATUS_CHANGED
                    # but nothing garantie that the event will be handled before cmd_func
                    # errors returns. If this happen, let's also close the connection
                    pass

                finally:
                    with trio.CancelScope(shield=True):
                        await self.invite.claimer_left(
                            organization_id=client_ctx.organization_id,
                            greeter=client_ctx.invitation.greeter_user_id,
                            token=client_ctx.invitation.token,
                        )

            else:
                await self._handle_client_websocket_loop(transport, client_ctx)

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

    async def _handle_client_websocket_loop(self, transport, client_ctx):
        # Retrieve the allowed commands according to api version and auth type
        api_cmds = self.apis[client_ctx.handshake_type]

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

                cmd_func = api_cmds[cmd]

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
                client_ctx.logger.debug("Response", rep=_filter_binary_fields(rep))
            else:
                client_ctx.logger.info("Request", cmd=cmd, status=rep["status"])
            raw_rep = packb(rep)
            await transport.send(raw_rep)
            raw_req = None
