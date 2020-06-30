# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from functools import partial

import pytest
import trio

from parsec.api.protocol.base import MsgpackSerializer
from parsec.api.transport import Transport, TransportClosedByPeer
from parsec.serde import BaseSchema, fields


@pytest.fixture
async def serve_tcp_testbed(unused_tcp_port):
    async def _serve_tcp_testbed(*conns):
        host = "127.0.0.1"
        send_channel, receive_channel = trio.open_memory_channel(0)

        async def _serve_client(stream):
            server_fn = await receive_channel.receive()
            transport = await Transport.init_for_server(stream)
            await server_fn(transport)

        async def _store_handlers(*, task_status=trio.TASK_STATUS_IGNORED):
            async with trio.open_service_nursery() as handler_nursery:
                task_status.started(handler_nursery)
                await trio.sleep_forever()

        async with trio.open_service_nursery() as nursery:
            handler_nursery = await nursery.start(_store_handlers)
            await nursery.start(
                partial(
                    trio.serve_tcp, _serve_client, unused_tcp_port, handler_nursery=handler_nursery
                )
            )
            assert not handler_nursery.child_tasks

            for client_fn, server_fn in conns:
                stream = await trio.open_tcp_stream(host, unused_tcp_port)
                await send_channel.send(server_fn)

                transport = await Transport.init_for_client(stream, host)
                await client_fn(transport)

                await trio.testing.wait_all_tasks_blocked()
                # No pending connections should remain
                assert not handler_nursery.child_tasks

            await send_channel.aclose()
            nursery.cancel_scope.cancel()

    return _serve_tcp_testbed


@pytest.mark.trio
@pytest.mark.parametrize("closing_end", ["client", "server"])
async def test_no_transport_leaks_one_end_close(serve_tcp_testbed, closing_end):
    async def closing_end_fn(transport):
        with pytest.raises(TransportClosedByPeer):
            await transport.recv()
        await transport.aclose()

    async def listening_end_fn(transport):
        await transport.aclose()

    if closing_end == "server":
        await serve_tcp_testbed((listening_end_fn, closing_end_fn))
    else:
        await serve_tcp_testbed((closing_end_fn, listening_end_fn))


# TODO: basically a benchmark to showcase the performances issues with
# marshmallow/json serialization
@pytest.mark.slow
@pytest.mark.trio
async def test_big_buffer_bench(backend_addr):
    server_stream, client_stream = trio.testing.memory_stream_pair()

    client_transport = None
    server_transport = None

    async def _boot_server():
        nonlocal server_transport
        server_transport = await Transport.init_for_server(server_stream)

    async def _boot_client():
        nonlocal client_transport
        client_transport = await Transport.init_for_client(
            client_stream, host=backend_addr.hostname
        )

    async with trio.open_service_nursery() as nursery:
        nursery.start_soon(_boot_client)
        nursery.start_soon(_boot_server)

    class Schema(BaseSchema):
        data = fields.Bytes()

    schema = MsgpackSerializer(Schema)

    # Base64 encoding of the bytes make the payload bigger once serialized
    # roughly_max = int(TCPTransport.MAX_MSG_SIZE * 2 / 3)
    roughly_max = int(Transport.RECEIVE_BYTES * 2 / 3)
    payload = {"data": b"x" * roughly_max}

    for _ in range(10):
        await client_transport.send(schema.dumps(payload))
        raw = await server_transport.recv()
        server_payload = schema.loads(raw)
        assert server_payload == payload
        del raw

        await server_transport.send(schema.dumps(server_payload))
        raw = await client_transport.recv()
        roundtrip_payload = schema.loads(raw)
        assert roundtrip_payload == payload
        del raw


# TODO: test websocket can work with message sent across mutiple TCP frames


@pytest.mark.trio
async def test_send_http_request(running_backend):
    stream = await trio.open_tcp_stream(running_backend.addr.hostname, running_backend.addr.port)
    await stream.send_all(
        b"GET / HTTP/1.1\r\n"
        b"Host: parsec.example.com\r\n"
        b"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:69.0) Gecko/20100101 Firefox/69.0\r\n"
        b"Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\n"
        b"Accept-Language: fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3\r\n"
        b"Accept-Encoding: gzip, deflate\r\n"
        b"DNT: 1\r\n"
        b"Connection: keep-alive\r\n"
        b"Upgrade-Insecure-Requests: 1\r\n"
        b"Cache-Control: max-age=0\r\n"
        b"\r\n"
    )
    rep = await stream.receive_some(4096)
    assert rep == (
        b"HTTP/1.1 426 OK\r\n"
        b"Upgrade: WebSocket\r\n"
        b"Content-Length: 51\r\n"
        b"Connection: Upgrade\r\n"
        b"Content-Type: text/html; charset=UTF-8\r\n"
        b"\r\n"
        b"This service requires use of the WebSocket protocol"
    )
    # Connection has been closed by peer
    with pytest.raises(trio.BrokenResourceError):
        await stream.send_all(b"dummy")
