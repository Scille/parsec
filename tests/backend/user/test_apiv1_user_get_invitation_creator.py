# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.api.protocol import apiv1_user_get_invitation_creator_serializer
from parsec.backend.user import INVITATION_VALIDITY, UserInvitation
from tests.common import freeze_time


@pytest.fixture
async def mallory_invitation(backend, alice, mallory):
    invitation = UserInvitation(user_id=mallory.user_id, creator=alice.device_id)
    await backend.user.create_user_invitation(alice.organization_id, invitation)
    return invitation


async def user_get_invitation_creator(sock, **kwargs):
    await sock.send(
        apiv1_user_get_invitation_creator_serializer.req_dumps(
            {"cmd": "user_get_invitation_creator", **kwargs}
        )
    )
    raw_rep = await sock.recv()
    return apiv1_user_get_invitation_creator_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_user_get_invitation_creator_too_late(
    apiv1_anonymous_backend_sock, mallory_invitation
):
    with freeze_time(mallory_invitation.created_on.add(seconds=INVITATION_VALIDITY + 1)):
        rep = await user_get_invitation_creator(
            apiv1_anonymous_backend_sock, invited_user_id=mallory_invitation.user_id
        )
    assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_user_get_invitation_creator_bad_id(apiv1_anonymous_backend_sock):
    rep = await user_get_invitation_creator(apiv1_anonymous_backend_sock, invited_user_id=42)
    assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_user_get_invitation_creator_unknown(apiv1_anonymous_backend_sock, mallory):
    rep = await user_get_invitation_creator(
        apiv1_anonymous_backend_sock, invited_user_id=mallory.user_id
    )
    assert rep == {"status": "not_found"}


# TODO: user_get_invitation_creator with a creator not certified by root


@pytest.mark.trio
async def test_user_get_invitation_creator_ok(
    certificates_store, alice, mallory_invitation, apiv1_anonymous_backend_sock
):
    rep = await user_get_invitation_creator(
        apiv1_anonymous_backend_sock, invited_user_id=mallory_invitation.user_id
    )
    assert rep == {
        "status": "ok",
        "device_certificate": certificates_store.get_device(alice),
        "user_certificate": certificates_store.get_user(alice),
        "trustchain": {"devices": [], "revoked_users": [], "users": []},
    }


@pytest.mark.trio
async def test_user_get_invitation_creator_with_trustchain_ok(
    certificates_store,
    backend_data_binder_factory,
    local_device_factory,
    backend,
    alice,
    mallory,
    apiv1_anonymous_backend_sock,
):
    binder = backend_data_binder_factory(backend)

    roger1 = local_device_factory("roger@dev1")
    mike1 = local_device_factory("mike@dev1")

    await binder.bind_device(roger1, certifier=alice)
    await binder.bind_device(mike1, certifier=roger1)
    await binder.bind_revocation(roger1.user_id, certifier=mike1)

    invitation = UserInvitation(user_id=mallory.user_id, creator=mike1.device_id)
    await backend.user.create_user_invitation(alice.organization_id, invitation)

    rep = await user_get_invitation_creator(
        apiv1_anonymous_backend_sock, invited_user_id=invitation.user_id
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
