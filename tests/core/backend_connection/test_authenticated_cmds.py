# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
import pytest
import trio

from parsec.api.data import RevokedUserCertificateContent
from parsec.api.protocol import AUTHENTICATED_CMDS, APIEvent, HandshakeType, ServerHandshake
from parsec.api.transport import Ping, Pong, Transport
from parsec.core.backend_connection import (
    BackendConnectionRefused,
    BackendNotAvailable,
    backend_authenticated_cmds_factory,
)
from parsec.core.types import BackendOrganizationAddr
from tests.core.backend_connection.common import ALL_CMDS


@pytest.mark.trio
async def test_backend_offline(alice):
    with pytest.raises(BackendNotAvailable):
        async with backend_authenticated_cmds_factory(
            alice.organization_addr, alice.device_id, alice.signing_key
        ) as cmds:
            await cmds.ping()


@pytest.mark.trio
async def test_backend_switch_offline(running_backend, alice):
    async with backend_authenticated_cmds_factory(
        alice.organization_addr, alice.device_id, alice.signing_key
    ) as cmds:
        await cmds.ping()
        with running_backend.offline():
            with pytest.raises(BackendNotAvailable):
                await cmds.ping()


@pytest.mark.trio
async def test_backend_closed_cmds(running_backend, alice):
    async with backend_authenticated_cmds_factory(
        alice.organization_addr, alice.device_id, alice.signing_key
    ) as cmds:
        pass
    with pytest.raises(trio.ClosedResourceError):
        await cmds.ping()


@pytest.mark.trio
async def test_ping(running_backend, alice):
    async with backend_authenticated_cmds_factory(
        alice.organization_addr, alice.device_id, alice.signing_key
    ) as cmds:
        rep = await cmds.ping("Hello World !")
        assert rep == {"status": "ok", "pong": "Hello World !"}


@pytest.mark.trio
async def test_handshake_unknown_device(running_backend, alice, mallory):
    with pytest.raises(BackendConnectionRefused) as exc:
        async with backend_authenticated_cmds_factory(
            alice.organization_addr, mallory.device_id, mallory.signing_key
        ) as cmds:
            await cmds.ping()
    assert str(exc.value) == "Invalid handshake information"


@pytest.mark.trio
async def test_handshake_unknown_organization(running_backend, alice):
    unknown_org_addr = BackendOrganizationAddr.build(
        backend_addr=alice.organization_addr,
        organization_id="dummy",
        root_verify_key=alice.organization_addr.root_verify_key,
    )
    with pytest.raises(BackendConnectionRefused) as exc:
        async with backend_authenticated_cmds_factory(
            unknown_org_addr, alice.device_id, alice.signing_key
        ) as cmds:
            await cmds.ping()
    assert str(exc.value) == "Invalid handshake information"


@pytest.mark.trio
async def test_handshake_rvk_mismatch(running_backend, alice, otherorg):
    bad_rvk_org_addr = BackendOrganizationAddr.build(
        backend_addr=alice.organization_addr,
        organization_id=alice.organization_id,
        root_verify_key=otherorg.root_verify_key,
    )
    with pytest.raises(BackendConnectionRefused) as exc:
        async with backend_authenticated_cmds_factory(
            bad_rvk_org_addr, alice.device_id, alice.signing_key
        ) as cmds:
            await cmds.ping()
    assert str(exc.value) == "Root verify key for organization differs between client and server"


@pytest.mark.trio
async def test_handshake_revoked_device(running_backend, alice, bob):
    revoked_user_certificate = RevokedUserCertificateContent(
        author=alice.device_id, timestamp=pendulum.now(), user_id=bob.user_id
    ).dump_and_sign(alice.signing_key)
    await running_backend.backend.user.revoke_user(
        organization_id=alice.organization_id,
        user_id=bob.user_id,
        revoked_user_certificate=revoked_user_certificate,
        revoked_user_certifier=alice.device_id,
    )

    with pytest.raises(BackendConnectionRefused) as exc:
        async with backend_authenticated_cmds_factory(
            bob.organization_addr, bob.device_id, bob.signing_key
        ) as cmds:
            await cmds.ping()
    assert str(exc.value) == "Device has been revoked"


@pytest.mark.trio
async def test_organization_expired(running_backend, alice, expiredorg):
    with pytest.raises(BackendConnectionRefused) as exc:
        async with backend_authenticated_cmds_factory(
            expiredorg.addr, alice.device_id, alice.signing_key
        ) as cmds:
            await cmds.ping()
    assert str(exc.value) == "Trial organization has expired"


@pytest.mark.trio
async def test_backend_disconnect_during_handshake(tcp_stream_spy, alice, backend_addr):
    client_answered = False

    async def poorly_serve_client(stream):
        nonlocal client_answered

        transport = await Transport.init_for_server(stream)
        handshake = ServerHandshake()
        await transport.send(handshake.build_challenge_req())
        await transport.recv()
        # Close connection during handshake
        await stream.aclose()

        client_answered = True

    async with trio.open_service_nursery() as nursery:

        async def connection_factory(*args, **kwargs):
            client_stream, server_stream = trio.testing.memory_stream_pair()
            nursery.start_soon(poorly_serve_client, server_stream)
            return client_stream

        with tcp_stream_spy.install_hook(backend_addr, connection_factory):
            with pytest.raises(BackendNotAvailable):
                async with backend_authenticated_cmds_factory(
                    alice.organization_addr, alice.device_id, alice.signing_key
                ) as cmds:
                    await cmds.ping()

        nursery.cancel_scope.cancel()

    assert client_answered


@pytest.mark.trio
async def test_events_listen_wait_has_watchdog(monkeypatch, mock_clock, running_backend, alice):
    # Spy on the transport events to detect the Pings/Pongs
    # (Note we are talking about websocket ping, not our own higher-level ping api)
    transport_events_sender, transport_events_receiver = trio.open_memory_channel(100)
    _vanilla_next_ws_event = Transport._next_ws_event

    async def _mocked_next_ws_event(transport):
        event = await _vanilla_next_ws_event(transport)
        await transport_events_sender.send((transport, event))
        return event

    monkeypatch.setattr(Transport, "_next_ws_event", _mocked_next_ws_event)

    async def next_ping_related_event():
        while True:
            transport, event = await transport_events_receiver.receive()
            if isinstance(event, (Ping, Pong)):
                return (transport, event)

    # Highjack the backend api to control the wait time and the final
    # event that will be returned to the client
    backend_received_cmd = trio.Event()
    backend_client_ctx = None
    vanilla_api_events_listen = running_backend.backend.apis[HandshakeType.AUTHENTICATED][
        "events_listen"
    ]

    async def _mocked_api_events_listen(client_ctx, msg):
        nonlocal backend_client_ctx
        backend_client_ctx = client_ctx
        backend_received_cmd.set()
        return await vanilla_api_events_listen(client_ctx, msg)

    running_backend.backend.apis[HandshakeType.AUTHENTICATED][
        "events_listen"
    ] = _mocked_api_events_listen

    events_listen_rep = None
    async with backend_authenticated_cmds_factory(
        alice.organization_addr, alice.device_id, alice.signing_key, keepalive=2
    ) as cmds:
        mock_clock.rate = 1
        async with trio.open_service_nursery() as nursery:

            async def _cmd():
                nonlocal events_listen_rep
                events_listen_rep = await cmds.events_listen(wait=True)

            nursery.start_soon(_cmd)

            # Wait for the connection to be established with the backend
            with trio.fail_after(1):
                await backend_received_cmd.wait()

            # Now advance time until ping is requested
            await trio.testing.wait_all_tasks_blocked()
            mock_clock.jump(2)
            with trio.fail_after(2):
                backend_transport, event = await next_ping_related_event()
                assert isinstance(event, Ping)
                client_transport, event = await next_ping_related_event()
                assert isinstance(event, Pong)
                assert client_transport is not backend_transport

            # Wait for another ping, just to be sure...
            await trio.testing.wait_all_tasks_blocked()
            mock_clock.jump(2)
            with trio.fail_after(1):
                backend_transport2, event = await next_ping_related_event()
                assert isinstance(event, Ping)
                assert backend_transport is backend_transport2
                client_transport2, event = await next_ping_related_event()
                assert isinstance(event, Pong)
                assert client_transport is client_transport2

            await backend_client_ctx.send_events_channel.send(
                {"event": APIEvent.PINGED, "ping": "foo"}
            )

    assert events_listen_rep == {"status": "ok", "event": APIEvent.PINGED, "ping": "foo"}


@pytest.mark.trio
async def test_authenticated_cmds_has_right_methods(running_backend, alice):
    async with backend_authenticated_cmds_factory(
        alice.organization_addr, alice.device_id, alice.signing_key
    ) as cmds:
        for method_name in AUTHENTICATED_CMDS:
            assert hasattr(cmds, method_name)
        for method_name in ALL_CMDS - AUTHENTICATED_CMDS:
            assert not hasattr(cmds, method_name)
