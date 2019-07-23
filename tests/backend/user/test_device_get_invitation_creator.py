# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum

from parsec.types import DeviceID
from parsec.api.protocol import device_get_invitation_creator_serializer
from parsec.backend.user import DeviceInvitation, INVITATION_VALIDITY

from tests.common import freeze_time


@pytest.fixture
async def alice_nd_invitation(backend, alice):
    invitation = DeviceInvitation(
        DeviceID(f"{alice.user_id}@new_device"), alice.device_id, Pendulum(2000, 1, 2)
    )
    await backend.user.create_device_invitation(alice.organization_id, invitation)
    return invitation


async def device_get_invitation_creator(sock, **kwargs):
    await sock.send(
        device_get_invitation_creator_serializer.req_dumps(
            {"cmd": "device_get_invitation_creator", **kwargs}
        )
    )
    raw_rep = await sock.recv()
    return device_get_invitation_creator_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_device_get_invitation_creator_too_late(anonymous_backend_sock, alice_nd_invitation):
    with freeze_time(alice_nd_invitation.created_on.add(seconds=INVITATION_VALIDITY + 1)):
        rep = await device_get_invitation_creator(
            anonymous_backend_sock, invited_device_id=alice_nd_invitation.device_id
        )
    assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_device_get_invitation_creator_unknown(anonymous_backend_sock, mallory):
    rep = await device_get_invitation_creator(
        anonymous_backend_sock, invited_device_id=mallory.device_id
    )
    assert rep == {"status": "not_found"}


# TODO: device_get_invitation_creator with a creator not certified by root


@pytest.mark.trio
async def test_device_get_invitation_creator_bad_id(anonymous_backend_sock):
    rep = await device_get_invitation_creator(anonymous_backend_sock, invited_device_id="dummy")
    assert rep == {
        "status": "bad_message",
        "reason": "Invalid message.",
        "errors": {"invited_device_id": ["Invalid device ID"]},
    }


@pytest.mark.trio
async def test_device_get_invitation_creator_ok(
    backend_data_binder_factory, backend, alice_nd_invitation, alice, anonymous_backend_sock
):
    binder = backend_data_binder_factory(backend)

    with freeze_time(alice_nd_invitation.created_on):
        rep = await device_get_invitation_creator(
            anonymous_backend_sock, invited_device_id=alice_nd_invitation.device_id
        )
    assert rep == {
        "status": "ok",
        "device_certificate": binder.certificates_store.get_device(alice),
        "user_certificate": binder.certificates_store.get_user(alice),
        "trustchain": [],
    }


@pytest.mark.trio
async def test_device_get_invitation_creator_with_trustchain_ok(
    backend_data_binder_factory, local_device_factory, backend, alice, anonymous_backend_sock
):
    binder = backend_data_binder_factory(backend)
    certificates_store = binder.certificates_store

    roger1 = local_device_factory("roger@dev1")
    mike1 = local_device_factory("mike@dev1")

    await binder.bind_device(roger1, certifier=alice)
    await binder.bind_device(mike1, certifier=roger1)
    await binder.bind_revocation(mike1, certifier=roger1)

    invitation = DeviceInvitation(
        device_id=DeviceID(f"{alice.user_id}@new"), creator=mike1.device_id
    )
    await backend.user.create_device_invitation(alice.organization_id, invitation)

    rep = await device_get_invitation_creator(
        anonymous_backend_sock, invited_device_id=invitation.device_id
    )
    rep["trustchain"] = sorted(rep["trustchain"], key=lambda x: x["device_id"])
    assert rep == {
        "status": "ok",
        "device_certificate": certificates_store.get_device(mike1),
        "user_certificate": certificates_store.get_user(mike1),
        "trustchain": [
            {
                "device_id": alice.device_id,
                "device_certificate": certificates_store.get_device(alice),
                "revoked_device_certificate": certificates_store.get_revoked_device(alice),
            },
            {
                "device_id": roger1.device_id,
                "device_certificate": certificates_store.get_device(roger1),
                "revoked_device_certificate": certificates_store.get_revoked_device(roger1),
            },
        ],
    }
