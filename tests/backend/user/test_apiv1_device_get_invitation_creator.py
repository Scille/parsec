# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum

from parsec.api.protocol import apiv1_device_get_invitation_creator_serializer
from parsec.backend.user import INVITATION_VALIDITY, DeviceInvitation
from tests.common import freeze_time


@pytest.fixture
async def alice_nd_invitation(backend, alice):
    invitation = DeviceInvitation(
        alice.user_id.to_device_id("new_device"), alice.device_id, Pendulum(2000, 1, 2)
    )
    await backend.user.create_device_invitation(alice.organization_id, invitation)
    return invitation


async def device_get_invitation_creator(sock, **kwargs):
    await sock.send(
        apiv1_device_get_invitation_creator_serializer.req_dumps(
            {"cmd": "device_get_invitation_creator", **kwargs}
        )
    )
    raw_rep = await sock.recv()
    return apiv1_device_get_invitation_creator_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_device_get_invitation_creator_too_late(
    apiv1_anonymous_backend_sock, alice_nd_invitation
):
    with freeze_time(alice_nd_invitation.created_on.add(seconds=INVITATION_VALIDITY + 1)):
        rep = await device_get_invitation_creator(
            apiv1_anonymous_backend_sock, invited_device_id=alice_nd_invitation.device_id
        )
    assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_device_get_invitation_creator_unknown(apiv1_anonymous_backend_sock, mallory):
    rep = await device_get_invitation_creator(
        apiv1_anonymous_backend_sock, invited_device_id=mallory.device_id
    )
    assert rep == {"status": "not_found"}


# TODO: device_get_invitation_creator with a creator not certified by root


@pytest.mark.trio
async def test_device_get_invitation_creator_bad_id(apiv1_anonymous_backend_sock):
    rep = await device_get_invitation_creator(
        apiv1_anonymous_backend_sock, invited_device_id="dummy"
    )
    assert rep == {
        "status": "bad_message",
        "reason": "Invalid message.",
        "errors": {"invited_device_id": ["Invalid device ID"]},
    }


@pytest.mark.trio
async def test_device_get_invitation_creator_ok(
    backend_data_binder_factory, backend, alice_nd_invitation, alice, apiv1_anonymous_backend_sock
):
    binder = backend_data_binder_factory(backend)

    with freeze_time(alice_nd_invitation.created_on):
        rep = await device_get_invitation_creator(
            apiv1_anonymous_backend_sock, invited_device_id=alice_nd_invitation.device_id
        )
    assert rep == {
        "status": "ok",
        "device_certificate": binder.certificates_store.get_device(alice),
        "user_certificate": binder.certificates_store.get_user(alice),
        "trustchain": {"devices": [], "revoked_users": [], "users": []},
    }


@pytest.mark.trio
async def test_device_get_invitation_creator_with_trustchain_ok(
    backend_data_binder_factory, local_device_factory, backend, alice, apiv1_anonymous_backend_sock
):
    binder = backend_data_binder_factory(backend)
    certificates_store = binder.certificates_store

    roger1 = local_device_factory("roger@dev1")
    mike1 = local_device_factory("mike@dev1")

    await binder.bind_device(roger1, certifier=alice)
    await binder.bind_device(mike1, certifier=roger1)
    await binder.bind_revocation(roger1.user_id, certifier=mike1)

    invitation = DeviceInvitation(
        device_id=alice.user_id.to_device_id("new"), creator=mike1.device_id
    )
    await backend.user.create_device_invitation(alice.organization_id, invitation)

    rep = await device_get_invitation_creator(
        apiv1_anonymous_backend_sock, invited_device_id=invitation.device_id
    )
    cooked_rep = {
        **rep,
        "device_certificate": certificates_store.translate_certif(rep["device_certificate"]),
        "user_certificate": certificates_store.translate_certif(rep["user_certificate"]),
        "trustchain": {
            "devices": sorted(certificates_store.translate_certifs(rep["trustchain"]["devices"])),
            "users": sorted(certificates_store.translate_certifs(rep["trustchain"]["users"])),
            "revoked_users": sorted(
                certificates_store.translate_certifs(rep["trustchain"]["revoked_users"])
            ),
        },
    }

    assert cooked_rep == {
        "status": "ok",
        "device_certificate": "<mike@dev1 device certif>",
        "user_certificate": "<mike user certif>",
        "trustchain": {
            "devices": sorted(
                [
                    "<alice@dev1 device certif>",
                    "<roger@dev1 device certif>",
                    "<mike@dev1 device certif>",
                ]
            ),
            "users": sorted(["<alice user certif>", "<roger user certif>", "<mike user certif>"]),
            "revoked_users": ["<roger revoked user certif>"],
        },
    }
