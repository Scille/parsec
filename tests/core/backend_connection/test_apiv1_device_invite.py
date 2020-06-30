# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
import pytest
import trio

from parsec.api.data import (
    APIV1_DeviceClaimAnswerContent,
    APIV1_DeviceClaimContent,
    DeviceCertificateContent,
    UserCertificateContent,
)
from parsec.backend.backend_events import BackendEvent
from parsec.core.backend_connection import (
    apiv1_backend_anonymous_cmds_factory,
    backend_authenticated_cmds_factory,
)
from parsec.crypto import PrivateKey, SigningKey
from parsec.event_bus import MetaEvent


@pytest.mark.trio
async def test_device_invite_then_claim_ok(alice, apiv1_alice_backend_cmds, running_backend):
    nd_id = alice.user_id.to_device_id("new_device")
    nd_signing_key = SigningKey.generate()
    token = "123456"
    device_certificate = None

    async def _alice_invite():
        nonlocal device_certificate

        ret = await apiv1_alice_backend_cmds.device_invite(nd_id.device_name)
        assert ret["status"] == "ok"
        claim = APIV1_DeviceClaimContent.decrypt_and_load_for(
            ret["encrypted_claim"], recipient_privkey=alice.private_key
        )

        assert claim.token == token

        device_certificate = DeviceCertificateContent(
            author=alice.device_id,
            timestamp=pendulum.now(),
            device_id=claim.device_id,
            device_label=None,
            verify_key=claim.verify_key,
        ).dump_and_sign(alice.signing_key)
        encrypted_answer = APIV1_DeviceClaimAnswerContent(
            private_key=alice.private_key,
            user_manifest_id=alice.user_manifest_id,
            user_manifest_key=alice.user_manifest_key,
        ).dump_and_encrypt_for(recipient_pubkey=claim.answer_public_key)
        with trio.fail_after(1):
            ret = await apiv1_alice_backend_cmds.device_create(device_certificate, encrypted_answer)
            assert ret["status"] == "ok"

    async def _alice_nd_claim():
        async with apiv1_backend_anonymous_cmds_factory(alice.organization_addr) as cmds:
            ret = await cmds.device_get_invitation_creator(nd_id)
            assert ret["status"] == "ok"
            assert ret["trustchain"] == {"devices": [], "revoked_users": [], "users": []}
            creator = UserCertificateContent.unsecure_load(ret["user_certificate"])
            creator_device = DeviceCertificateContent.unsecure_load(ret["device_certificate"])
            assert creator_device.device_id.user_id == creator.user_id

            answer_private_key = PrivateKey.generate()
            encrypted_claim = APIV1_DeviceClaimContent(
                token=token,
                device_id=nd_id,
                verify_key=nd_signing_key.verify_key,
                answer_public_key=answer_private_key.public_key,
            ).dump_and_encrypt_for(recipient_pubkey=creator.public_key)
            with trio.fail_after(1):
                ret = await cmds.device_claim(nd_id, encrypted_claim)
                assert ret["status"] == "ok"

            assert ret["device_certificate"] == device_certificate
            answer = APIV1_DeviceClaimAnswerContent.decrypt_and_load_for(
                ret["encrypted_answer"], recipient_privkey=answer_private_key
            )
            assert answer == APIV1_DeviceClaimAnswerContent(
                private_key=alice.private_key,
                user_manifest_id=alice.user_manifest_id,
                user_manifest_key=alice.user_manifest_key,
            )

    with running_backend.backend.event_bus.listen() as spy:
        async with trio.open_service_nursery() as nursery:
            nursery.start_soon(_alice_invite)
            await spy.wait_with_timeout(
                MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.DEVICE_CLAIMED}
            )
            nursery.start_soon(_alice_nd_claim)

    # Now alice's new device should be able to connect to backend
    async with backend_authenticated_cmds_factory(
        alice.organization_addr, nd_id, nd_signing_key
    ) as cmds:
        ret = await cmds.ping("Hello World !")
        assert ret == {"pong": "Hello World !", "status": "ok"}
