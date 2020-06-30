# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pytest

from parsec.api.protocol.base import InvalidMessageError, packb, unpackb
from parsec.api.protocol.handshake import (
    APIV1_AdministrationClientHandshake,
    APIV1_AnonymousClientHandshake,
    APIV1_AuthenticatedClientHandshake,
    APIV1_HandshakeType,
    BaseClientHandshake,
    HandshakeAPIVersionError,
    HandshakeBadAdministrationToken,
    HandshakeBadIdentity,
    HandshakeFailedChallenge,
    HandshakeOrganizationExpired,
    HandshakeRevokedDevice,
    HandshakeRVKMismatch,
    ServerHandshake,
)
from parsec.api.version import API_V1_VERSION, ApiVersion


def test_good_handshake(alice):
    sh = ServerHandshake()

    ch = APIV1_AuthenticatedClientHandshake(
        alice.organization_id, alice.device_id, alice.signing_key, alice.root_verify_key
    )
    assert sh.state == "stalled"

    challenge_req = sh.build_challenge_req()
    assert sh.state == "challenge"

    answer_req = ch.process_challenge_req(challenge_req)

    sh.process_answer_req(answer_req)
    assert sh.state == "answer"
    assert sh.answer_type == APIV1_HandshakeType.AUTHENTICATED
    assert sh.answer_data == {
        "answer": ANY,
        "client_api_version": API_V1_VERSION,
        "organization_id": alice.organization_id,
        "device_id": alice.device_id,
        "rvk": alice.root_verify_key,
    }
    result_req = sh.build_result_req(alice.verify_key)
    assert sh.state == "result"

    ch.process_result_req(result_req)
    assert sh.client_api_version == API_V1_VERSION


@pytest.mark.parametrize("check_rvk", (True, False))
def test_good_anonymous_handshake(coolorg, check_rvk):
    sh = ServerHandshake()

    if check_rvk:
        ch = APIV1_AnonymousClientHandshake(coolorg.organization_id, coolorg.root_verify_key)
    else:
        ch = APIV1_AnonymousClientHandshake(coolorg.organization_id)
    assert sh.state == "stalled"

    challenge_req = sh.build_challenge_req()
    assert sh.state == "challenge"

    answer_req = ch.process_challenge_req(challenge_req)

    sh.process_answer_req(answer_req)
    assert sh.state == "answer"
    assert sh.answer_type == APIV1_HandshakeType.ANONYMOUS
    if check_rvk:
        assert sh.answer_data == {
            "client_api_version": API_V1_VERSION,
            "organization_id": coolorg.organization_id,
            "rvk": coolorg.root_verify_key,
        }
    else:
        assert sh.answer_data == {
            "client_api_version": API_V1_VERSION,
            "organization_id": coolorg.organization_id,
            "rvk": None,
        }
    result_req = sh.build_result_req()
    assert sh.state == "result"

    ch.process_result_req(result_req)
    assert sh.client_api_version == API_V1_VERSION


def test_good_administration_handshake():
    admin_token = "Xx" * 16
    sh = ServerHandshake()

    ch = APIV1_AdministrationClientHandshake(admin_token)
    assert sh.state == "stalled"

    challenge_req = sh.build_challenge_req()
    assert sh.state == "challenge"

    answer_req = ch.process_challenge_req(challenge_req)

    sh.process_answer_req(answer_req)
    assert sh.state == "answer"
    assert sh.answer_type == APIV1_HandshakeType.ADMINISTRATION
    assert sh.answer_data == {"client_api_version": API_V1_VERSION, "token": admin_token}
    result_req = sh.build_result_req()
    assert sh.state == "result"

    ch.process_result_req(result_req)
    assert sh.client_api_version == API_V1_VERSION


# 1) Server build challenge (nothing more to test...)


# 2) Client process challenge


@pytest.mark.parametrize(
    "req",
    [
        {},
        {
            "handshake": "foo",
            "challenge": b"1234567890",
            "supported_api_versions": [API_V1_VERSION],
        },
        {"handshake": "challenge", "challenge": b"1234567890"},
        {"challenge": b"1234567890"},
        {"challenge": b"1234567890", "supported_api_versions": [API_V1_VERSION]},
        {"handshake": "challenge", "challenge": None},
        {"handshake": "challenge", "challenge": None, "supported_api_versions": [API_V1_VERSION]},
        {"handshake": "challenge", "challenge": 42, "supported_api_versions": [API_V1_VERSION]},
        {"handshake": "challenge", "challenge": b"1234567890"},
        {"handshake": "challenge", "challenge": b"1234567890", "supported_api_versions": "invalid"},
    ],
)
def test_process_challenge_req_bad_format(alice, req):
    ch = APIV1_AuthenticatedClientHandshake(
        alice.organization_id, alice.device_id, alice.signing_key, alice.root_verify_key
    )
    with pytest.raises(InvalidMessageError):
        ch.process_challenge_req(packb(req))


# 2-b) Client check API version


@pytest.mark.parametrize(
    "client_version, backend_version, valid",
    [
        ((2, 22), (1, 0), False),
        ((2, 22), (1, 111), False),
        ((2, 22), (2, 0), True),
        ((2, 22), (2, 22), True),
        ((2, 22), (2, 222), True),
        ((2, 22), (3, 0), False),
        ((2, 22), (3, 33), False),
        ((2, 22), (3, 333), False),
    ],
    ids=str,
)
def test_process_challenge_req_good_api_version(
    alice, monkeypatch, client_version, backend_version, valid
):
    # Cast parameters
    client_version = ApiVersion(*client_version)
    backend_version = ApiVersion(*backend_version)

    ch = APIV1_AuthenticatedClientHandshake(
        alice.organization_id, alice.device_id, alice.signing_key, alice.root_verify_key
    )
    req = {
        "handshake": "challenge",
        "challenge": b"1234567890",
        "supported_api_versions": [backend_version],
    }
    monkeypatch.setattr(ch, "SUPPORTED_API_VERSIONS", [client_version])

    # Invalid versioning
    if not valid:
        with pytest.raises(HandshakeAPIVersionError) as context:
            ch.process_challenge_req(packb(req))
        assert context.value.client_versions == [client_version]
        assert context.value.backend_versions == [backend_version]
        return

    # Valid versioning
    ch.process_challenge_req(packb(req))
    assert ch.SUPPORTED_API_VERSIONS == [client_version]
    assert ch.challenge_data["supported_api_versions"] == [backend_version]
    assert ch.backend_api_version == backend_version
    assert ch.client_api_version == client_version


@pytest.mark.parametrize(
    "client_versions, backend_versions, expected_client_version, expected_backend_version",
    [
        ([(2, 22), (3, 33)], [(0, 000), (1, 111)], None, None),
        ([(2, 22), (3, 33)], [(1, 111), (2, 222)], (2, 22), (2, 222)),
        ([(2, 22), (3, 33)], [(2, 222), (3, 333)], (3, 33), (3, 333)),
        ([(2, 22), (3, 33)], [(3, 333), (4, 444)], (3, 33), (3, 333)),
        ([(2, 22), (3, 33)], [(4, 444), (5, 555)], None, None),
        ([(2, 22), (4, 44)], [(1, 111), (2, 222)], (2, 22), (2, 222)),
        ([(2, 22), (4, 44)], [(1, 111), (3, 333)], None, None),
        ([(2, 22), (4, 44)], [(2, 222), (3, 333)], (2, 22), (2, 222)),
        ([(2, 22), (4, 44)], [(2, 222), (4, 444)], (4, 44), (4, 444)),
        ([(2, 22), (4, 44)], [(3, 333), (4, 444)], (4, 44), (4, 444)),
        ([(2, 22), (4, 44)], [(3, 333), (5, 555)], None, None),
        ([(2, 22), (4, 44)], [(4, 444), (5, 555)], (4, 44), (4, 444)),
        ([(2, 22), (4, 44)], [(4, 444), (6, 666)], (4, 44), (4, 444)),
        ([(2, 22), (4, 44)], [(5, 555), (6, 666)], None, None),
    ],
    ids=str,
)
def test_process_challenge_req_good_multiple_api_version(
    alice,
    monkeypatch,
    client_versions,
    backend_versions,
    expected_client_version,
    expected_backend_version,
):
    # Cast parameters
    client_versions = [ApiVersion(*args) for args in client_versions]
    backend_versions = [ApiVersion(*args) for args in backend_versions]
    if expected_client_version:
        expected_client_version = ApiVersion(*expected_client_version)
    if expected_backend_version:
        expected_backend_version = ApiVersion(*expected_backend_version)

    ch = APIV1_AuthenticatedClientHandshake(
        alice.organization_id, alice.device_id, alice.signing_key, alice.root_verify_key
    )
    req = {
        "handshake": "challenge",
        "challenge": b"1234567890",
        "supported_api_versions": list(backend_versions),
    }
    monkeypatch.setattr(ch, "SUPPORTED_API_VERSIONS", client_versions)

    # Invalid versioning
    if expected_client_version is None:
        with pytest.raises(HandshakeAPIVersionError) as context:
            ch.process_challenge_req(packb(req))
        assert context.value.client_versions == client_versions
        assert context.value.backend_versions == backend_versions
        return

    # Valid versioning
    ch.process_challenge_req(packb(req))
    assert ch.SUPPORTED_API_VERSIONS == client_versions
    assert ch.challenge_data["supported_api_versions"] == list(backend_versions)
    assert ch.backend_api_version == expected_backend_version
    assert ch.client_api_version == expected_client_version


# 3) Server process answer


@pytest.mark.parametrize(
    "req",
    [
        {},
        {"handshake": "answer", "type": "dummy"},  # Invalid type
        # Authenticated answer
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.AUTHENTICATED.value,
            "organization_id": "<good>",
            "device_id": "<good>",
            # Missing rvk
            "answer": b"good answer",
        },
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.AUTHENTICATED.value,
            "organization_id": "<good>",
            # Missing device_id
            "rvk": "<good>",
            "answer": b"good answer",
        },
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.AUTHENTICATED.value,
            "organization_id": "<good>",
            "device_id": "<good>",
            "rvk": "<good>",
            # Missing answer
        },
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.AUTHENTICATED.value,
            "organization_id": "<good>",
            "device_id": "<good>",
            "rvk": "<good>",
            "answer": 42,  # Bad type
        },
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.AUTHENTICATED.value,
            "organization_id": "<good>",
            "device_id": "dummy",  # Invalid DeviceID
            "rvk": "<good>",
            "answer": b"good answer",
        },
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.AUTHENTICATED.value,
            "organization_id": "<good>",
            "device_id": "<good>",
            "rvk": b"dummy",  # Invalid VerifyKey
            "answer": b"good answer",
        },
        # Anonymous answer
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.ANONYMOUS.value,
            "organization_id": "<good>",
            "rvk": b"dummy",  # Invalid VerifyKey
        },
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.ANONYMOUS.value,
            "organization_id": "d@mmy",  # Invalid OrganizationID
            "rvk": "<good>",
        },
        # Admin answer
        {
            "handshake": "answer",
            "type": APIV1_HandshakeType.ADMINISTRATION.value,
            # Missing token
        },
    ],
)
def test_process_answer_req_bad_format(req, alice):
    for key, good_value in [
        ("organization_id", alice.organization_id),
        ("device_id", alice.device_id),
        ("rvk", alice.root_verify_key.encode()),
    ]:
        if req.get(key) == "<good>":
            req[key] = good_value
    req["client_api_version"] = API_V1_VERSION
    sh = ServerHandshake()
    sh.build_challenge_req()
    with pytest.raises(InvalidMessageError):
        sh.process_answer_req(packb(req))


# 4) Server build result


def test_build_result_req_bad_key(alice, bob):
    sh = ServerHandshake()
    sh.build_challenge_req()
    answer = {
        "handshake": "answer",
        "type": "authenticated",
        "client_api_version": API_V1_VERSION,
        "organization_id": alice.organization_id,
        "device_id": alice.device_id,
        "rvk": alice.root_verify_key.encode(),
        "answer": alice.signing_key.sign(sh.challenge),
    }
    sh.process_answer_req(packb(answer))
    with pytest.raises(HandshakeFailedChallenge):
        sh.build_result_req(bob.verify_key)


def test_build_result_req_bad_challenge(alice):
    sh = ServerHandshake()
    sh.build_challenge_req()
    answer = {
        "handshake": "answer",
        "type": "authenticated",
        "client_api_version": API_V1_VERSION,
        "organization_id": alice.organization_id,
        "device_id": alice.device_id,
        "rvk": alice.root_verify_key.encode(),
        "answer": alice.signing_key.sign(sh.challenge + b"-dummy"),
    }
    sh.process_answer_req(packb(answer))
    with pytest.raises(HandshakeFailedChallenge):
        sh.build_result_req(alice.verify_key)


@pytest.mark.parametrize(
    "method,expected_result",
    [
        ("build_bad_protocol_result_req", "bad_protocol"),
        ("build_bad_identity_result_req", "bad_identity"),
        ("build_organization_expired_result_req", "organization_expired"),
        ("build_rvk_mismatch_result_req", "rvk_mismatch"),
        ("build_revoked_device_result_req", "revoked_device"),
        ("build_bad_administration_token_result_req", "bad_admin_token"),
    ],
)
def test_build_bad_outcomes(alice, method, expected_result):
    sh = ServerHandshake()
    sh.build_challenge_req()
    answer = {
        "handshake": "answer",
        "type": "authenticated",
        "client_api_version": API_V1_VERSION,
        "organization_id": alice.organization_id,
        "device_id": alice.device_id,
        "rvk": alice.root_verify_key.encode(),
        "answer": alice.signing_key.sign(sh.challenge),
    }
    sh.process_answer_req(packb(answer))
    req = getattr(sh, method)()
    assert unpackb(req) == {"handshake": "result", "result": expected_result, "help": ANY}


# 5) Client process result


@pytest.mark.parametrize(
    "req",
    [
        {},
        {"handshake": "foo", "result": "ok"},
        {"result": "ok"},
        {"handshake": "result", "result": "error"},
    ],
)
def test_process_result_req_bad_format(req):
    ch = BaseClientHandshake()
    with pytest.raises(InvalidMessageError):
        ch.process_result_req(packb(req))


@pytest.mark.parametrize(
    "result,exc_cls",
    [
        ("bad_identity", HandshakeBadIdentity),
        ("organization_expired", HandshakeOrganizationExpired),
        ("rvk_mismatch", HandshakeRVKMismatch),
        ("revoked_device", HandshakeRevokedDevice),
        ("bad_admin_token", HandshakeBadAdministrationToken),
        ("dummy", InvalidMessageError),
    ],
)
def test_process_result_req_bad_outcome(result, exc_cls):
    ch = BaseClientHandshake()
    with pytest.raises(exc_cls):
        ch.process_result_req(packb({"handshake": "result", "result": result}))


# TODO: test with revoked device
# TODO: test with user with all devices revoked
