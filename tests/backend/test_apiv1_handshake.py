# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.api.protocol import (
    APIV1_AdministrationClientHandshake,
    APIV1_AnonymousClientHandshake,
    APIV1_AuthenticatedClientHandshake,
    HandshakeBadAdministrationToken,
    HandshakeBadIdentity,
    HandshakeOrganizationExpired,
    HandshakeRVKMismatch,
    InvalidMessageError,
    ServerHandshake,
    packb,
    unpackb,
)
from parsec.api.transport import Transport
from parsec.api.version import ApiVersion


@pytest.fixture
def mock_api_versions(monkeypatch):
    default_client_version = ApiVersion(1, 11)
    default_backend_version = ApiVersion(1, 22)

    def _mock_api_versions(client_versions=None, backend_versions=None):
        if client_versions is not None:
            monkeypatch.setattr(
                APIV1_AuthenticatedClientHandshake, "SUPPORTED_API_VERSIONS", client_versions
            )
            monkeypatch.setattr(
                APIV1_AnonymousClientHandshake, "SUPPORTED_API_VERSIONS", client_versions
            )
            monkeypatch.setattr(
                APIV1_AdministrationClientHandshake, "SUPPORTED_API_VERSIONS", client_versions
            )
        if backend_versions is not None:
            monkeypatch.setattr(ServerHandshake, "SUPPORTED_API_VERSIONS", backend_versions)

    _mock_api_versions.default_client_version = default_client_version
    _mock_api_versions.default_backend_version = default_backend_version
    _mock_api_versions(
        client_versions=[default_client_version], backend_versions=[default_backend_version]
    )
    return _mock_api_versions


@pytest.mark.trio
async def test_anonymous_handshake_invalid_format(backend, server_factory):
    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        await transport.recv()  # Get challenge
        req = {
            "handshake": "foo",
            "type": "anonymous",
            "client_api_version": ApiVersion(1, 1),
            "organization_id": "zob",
        }
        await transport.send(packb(req))
        result_req = await transport.recv()
        assert unpackb(result_req) == {
            "handshake": "result",
            "result": "bad_protocol",
            "help": "{'handshake': ['Invalid value, should be `answer`']}",
        }


@pytest.mark.trio
async def test_authenticated_handshake_good(backend, server_factory, alice, mock_api_versions):
    ch = APIV1_AuthenticatedClientHandshake(
        alice.organization_id, alice.device_id, alice.signing_key, alice.root_verify_key
    )

    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        ch.process_result_req(result_req)

        assert ch.client_api_version == mock_api_versions.default_client_version
        assert ch.backend_api_version == mock_api_versions.default_backend_version


@pytest.mark.trio
async def test_administration_handshake_good(backend, server_factory, mock_api_versions):
    ch = APIV1_AdministrationClientHandshake(backend.config.administration_token)
    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        ch.process_result_req(result_req)

        assert ch.client_api_version == mock_api_versions.default_client_version
        assert ch.backend_api_version == mock_api_versions.default_backend_version


@pytest.mark.trio
async def test_administration_handshake_bad_token(backend, server_factory):
    ch = APIV1_AdministrationClientHandshake("dummy token")
    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        with pytest.raises(HandshakeBadAdministrationToken):
            ch.process_result_req(result_req)


@pytest.mark.trio
@pytest.mark.parametrize("is_anonymous", [True, False])
async def test_handshake_bad_rvk(backend, server_factory, coolorg, alice, otherorg, is_anonymous):
    if is_anonymous:
        ch = APIV1_AnonymousClientHandshake(coolorg.organization_id, otherorg.root_verify_key)
    else:
        ch = APIV1_AuthenticatedClientHandshake(
            alice.organization_id, alice.device_id, alice.signing_key, otherorg.root_verify_key
        )
    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        with pytest.raises(HandshakeRVKMismatch):
            ch.process_result_req(result_req)


@pytest.mark.trio
@pytest.mark.parametrize("check_rvk", [True, False])
async def test_anonymous_handshake_good(
    backend, server_factory, coolorg, check_rvk, mock_api_versions
):
    to_check_rvk = coolorg.root_verify_key if check_rvk else None
    ch = APIV1_AnonymousClientHandshake(coolorg.organization_id, to_check_rvk)
    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        ch.process_result_req(result_req)

        assert ch.client_api_version == mock_api_versions.default_client_version
        assert ch.backend_api_version == mock_api_versions.default_backend_version


@pytest.mark.trio
async def test_anonymous_handshake_bad_rvk(backend, server_factory, coolorg, otherorg):
    ch = APIV1_AnonymousClientHandshake(coolorg.organization_id, otherorg.root_verify_key)
    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        with pytest.raises(HandshakeRVKMismatch):
            ch.process_result_req(result_req)


@pytest.mark.trio
@pytest.mark.parametrize("type", ["anonymous", "authenticated"])
async def test_handshake_unknown_organization(
    backend, server_factory, organization_factory, alice, type
):
    bad_org = organization_factory()
    if type == "anonymous":
        ch = APIV1_AnonymousClientHandshake(bad_org.organization_id, bad_org.root_verify_key)
    else:  # authenticated
        ch = APIV1_AuthenticatedClientHandshake(
            bad_org.organization_id, alice.device_id, alice.signing_key, bad_org.root_verify_key
        )

    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        with pytest.raises(HandshakeBadIdentity):
            ch.process_result_req(result_req)


@pytest.mark.trio
async def test_authenticated_handshake_expired_organization(
    backend, server_factory, expiredorg, alice
):
    ch = APIV1_AuthenticatedClientHandshake(
        expiredorg.organization_id, alice.device_id, alice.signing_key, expiredorg.root_verify_key
    )

    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        with pytest.raises(HandshakeOrganizationExpired):
            ch.process_result_req(result_req)


@pytest.mark.trio
async def test_authenticated_handshake_unknown_device(backend, server_factory, mallory):
    ch = APIV1_AuthenticatedClientHandshake(
        mallory.organization_id, mallory.device_id, mallory.signing_key, mallory.root_verify_key
    )
    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        await transport.send(answer_req)
        result_req = await transport.recv()
        with pytest.raises(HandshakeBadIdentity):
            ch.process_result_req(result_req)


@pytest.mark.trio
async def test_authenticated_handshake_bad_versions(
    backend, server_factory, alice, mock_api_versions
):
    ch = APIV1_AuthenticatedClientHandshake(
        alice.organization_id, alice.device_id, alice.signing_key, alice.root_verify_key
    )

    async with server_factory(backend.handle_client) as server:
        stream = server.connection_factory()
        transport = await Transport.init_for_client(stream, server.addr.hostname)

        challenge_req = await transport.recv()
        answer_req = ch.process_challenge_req(challenge_req)

        # Alter answer
        answer_dict = unpackb(answer_req)
        answer_dict["client_api_version"] = ApiVersion(3, 22)
        answer_req = packb(answer_dict)

        await transport.send(answer_req)
        result_req = await transport.recv()

        with pytest.raises(InvalidMessageError) as context:
            ch.process_result_req(result_req)
        assert "bad_protocol" in str(context.value)
        assert "{1.22}" in str(context.value)
