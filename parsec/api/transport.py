# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional
from uuid import uuid4

import trio
from structlog import get_logger
from trio import BrokenResourceError
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, BytesMessage, CloseConnection, Ping, Pong, Request
from wsproto.frame_protocol import CloseReason
from wsproto.utilities import LocalProtocolError, RemoteProtocolError

__all__ = ("TransportError", "Transport")


logger = get_logger()
WEBSOCKET_HANDSHAKE_TIMEOUT = 3.0


class TransportError(Exception):
    pass


class TransportClosedByPeer(TransportError):
    pass


# Note we let `trio.ClosedResourceError` exceptions bubble up given
# they should be only raised in case of programming error.


class Transport:
    RECEIVE_BYTES = 2 ** 20  # 1Mo

    def __init__(self, stream, ws, keepalive: Optional[int] = None):
        self.stream = stream
        self.ws = ws
        self.keepalive = keepalive
        self.conn_id = uuid4().hex
        self.logger = logger.bind(conn_id=self.conn_id)
        self._ws_events = ws.events()
        self._handshake = None

    # Application handshake interface
    # TODO: Investigate a better place for providing an access to the peer API version
    # Note: This should not be confused with the websocket handshake

    @property
    def handshake(self):
        if self._handshake is None:
            raise TypeError("The handshake has not been set")
        return self._handshake

    @handshake.setter
    def handshake(self, handshake):
        if self._handshake is not None:
            raise TypeError("The handshake has already been set")
        self._handshake = handshake

    async def _next_ws_event(self):
        try:
            while True:
                try:
                    return next(self._ws_events)

                except StopIteration:
                    # Not enough data to form an event
                    await self._net_recv()
                    self._ws_events = self.ws.events()

        except RemoteProtocolError as exc:
            raise TransportError(f"Invalid WebSocket query: {exc}") from exc

    async def _net_recv(self):
        try:
            in_data = await self.stream.receive_some(self.RECEIVE_BYTES)

        except BrokenResourceError as exc:
            raise TransportError(*exc.args) from exc

        if not in_data:
            # A receive of zero bytes indicates the TCP socket has been closed. We
            # need to pass None to wsproto to update its internal state.
            self.ws.receive_data(None)
        else:
            self.ws.receive_data(in_data)

    async def _net_send(self, wsmsg):
        try:
            await self.stream.send_all(self.ws.send(wsmsg))

        except BrokenResourceError as exc:
            raise TransportError(*exc.args) from exc

        except RemoteProtocolError as exc:
            raise TransportError(*exc.args) from exc

    @classmethod
    async def init_for_client(cls, stream, host):
        ws = WSConnection(ConnectionType.CLIENT)
        transport = cls(stream, ws)

        # Because this is a client WebSocket, we need to initiate the connection
        # handshake by sending a Request event.
        await transport._net_send(Request(host=host, target="/ws"))

        # Get handshake answer
        event = await transport._next_ws_event()

        if isinstance(event, AcceptConnection):
            transport.logger.debug("WebSocket negotiation complete", ws_event=event)

        else:
            transport.logger.warning("Unexpected event during WebSocket handshake", ws_event=event)
            reason = f"Unexpected event during WebSocket handshake: {event}"
            raise TransportError(reason)

        return transport

    @classmethod
    async def init_for_server(cls, stream):
        ws = WSConnection(ConnectionType.SERVER)
        transport = cls(stream, ws)

        # Wait for client to init WebSocket handshake
        event = "Websocket handshake timeout"
        with trio.move_on_after(WEBSOCKET_HANDSHAKE_TIMEOUT):
            event = await transport._next_ws_event()

        if isinstance(event, Request):
            transport.logger.debug("Accepting WebSocket upgrade")
            await transport._net_send(AcceptConnection())
            return transport

        transport.logger.warning("Unexpected event during WebSocket handshake", ws_event=event)
        raise TransportError(f"Unexpected event during WebSocket handshake: {event}")

    async def aclose(self) -> None:
        try:
            try:
                await self.stream.send_all(
                    self.ws.send(CloseConnection(code=CloseReason.NORMAL_CLOSURE))
                )
            except LocalProtocolError:
                # TODO: exception occurs when ws.state is already closed...
                pass
            await self.stream.aclose()

        except (BrokenResourceError, TransportError):
            pass

    async def send(self, msg: bytes) -> None:
        """
        Raises:
            TransportError
        """
        await self._net_send(BytesMessage(data=msg))

    async def recv(self) -> bytes:
        """
        Raises:
            TransportError
        """
        data = bytearray()
        while True:
            if self.keepalive:
                with trio.move_on_after(self.keepalive) as cancel_scope:
                    event = await self._next_ws_event()
                if cancel_scope.cancel_called:
                    self.logger.debug("Sending keep alive ping")
                    await self._net_send(Ping())
                    continue
            else:
                event = await self._next_ws_event()

            if isinstance(event, CloseConnection):
                self.logger.debug("Connection closed", code=event.code, reason=event.reason)
                try:
                    await self._net_send(event.response())
                except LocalProtocolError:
                    # TODO: exception occurs when ws.state is already closed...
                    pass
                raise TransportClosedByPeer("Peer has closed connection")

            elif isinstance(event, BytesMessage):
                # TODO: check that data doesn't go over MAX_BIN_LEN (1 MB)
                # Msgpack will refuse to unpack it so we should fail early on if that happens
                data += event.data
                if event.message_finished:
                    return data

            elif isinstance(event, Ping):
                self.logger.debug("Received ping and sending pong")
                await self._net_send(event.response())

            elif isinstance(event, Pong):
                # Nothing to do \o/
                self.logger.debug("Received pong")

            else:
                self.logger.warning("Unexpected event", ws_event=event)
                raise TransportError(f"Unexpected event: {event}")
