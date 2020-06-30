# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
import pytest
import trio

from parsec.api.data import (
    APIV1_UserClaimContent,
    DeviceCertificateContent,
    UserCertificateContent,
    UserProfile,
)
from parsec.backend.backend_events import BackendEvent
from parsec.core.backend_connection import (
    apiv1_backend_anonymous_cmds_factory,
    backend_authenticated_cmds_factory,
)
from parsec.event_bus import MetaEvent


@pytest.mark.trio
async def test_user_invite_then_claim_ok(
    backend, running_backend, alice, apiv1_alice_backend_cmds, mallory
):
    token = "424242"

    async def _alice_invite():
        rep = await apiv1_alice_backend_cmds.user_invite(mallory.user_id)
        assert rep["status"] == "ok"
        claim = APIV1_UserClaimContent.decrypt_and_load_for(
            rep["encrypted_claim"], recipient_privkey=alice.private_key
        )

        assert claim.token == token

        now = pendulum.now()
        user_certificate = UserCertificateContent(
            author=alice.device_id,
            timestamp=now,
            user_id=claim.device_id.user_id,
            human_handle=None,
            public_key=claim.public_key,
            profile=UserProfile.STANDARD,
        ).dump_and_sign(alice.signing_key)
        device_certificate = DeviceCertificateContent(
            author=alice.device_id,
            timestamp=now,
            device_id=claim.device_id,
            device_label=None,
            verify_key=claim.verify_key,
        ).dump_and_sign(alice.signing_key)
        with trio.fail_after(1):
            rep = await apiv1_alice_backend_cmds.user_create(user_certificate, device_certificate)
            assert rep["status"] == "ok"

    async def _mallory_claim():
        async with apiv1_backend_anonymous_cmds_factory(mallory.organization_addr) as cmds:
            rep = await cmds.user_get_invitation_creator(mallory.user_id)
            assert rep["trustchain"] == {"devices": [], "revoked_users": [], "users": []}
            creator = UserCertificateContent.unsecure_load(rep["user_certificate"])
            creator_device = DeviceCertificateContent.unsecure_load(rep["device_certificate"])
            assert creator_device.device_id.user_id == creator.user_id

            encrypted_claim = APIV1_UserClaimContent(
                device_id=mallory.device_id,
                token=token,
                public_key=mallory.public_key,
                verify_key=mallory.verify_key,
            ).dump_and_encrypt_for(recipient_pubkey=creator.public_key)
            with trio.fail_after(1):
                rep = await cmds.user_claim(mallory.user_id, encrypted_claim)
                assert rep["status"] == "ok"

    with running_backend.backend.event_bus.listen() as spy:
        async with trio.open_service_nursery() as nursery:
            nursery.start_soon(_alice_invite)
            await spy.wait_with_timeout(
                MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_CLAIMED}
            )
            nursery.start_soon(_mallory_claim)

    # Now mallory should be able to connect to backend
    async with backend_authenticated_cmds_factory(
        mallory.organization_addr, mallory.device_id, mallory.signing_key
    ) as cmds:
        rep = await cmds.ping("Hello World !")
        assert rep == {"status": "ok", "pong": "Hello World !"}
