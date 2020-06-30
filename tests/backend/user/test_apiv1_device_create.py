# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
import pytest

from parsec.api.data import DeviceCertificateContent, UserProfile
from parsec.api.protocol import apiv1_device_create_serializer
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import INVITATION_VALIDITY, Device
from tests.backend.common import ping
from tests.common import customize_fixtures, freeze_time


async def device_create(sock, **kwargs):
    await sock.send(apiv1_device_create_serializer.req_dumps({"cmd": "device_create", **kwargs}))
    raw_rep = await sock.recv()
    rep = apiv1_device_create_serializer.rep_loads(raw_rep)
    return rep


@pytest.fixture
def alice_nd(local_device_factory, alice):
    return local_device_factory(f"{alice.user_id}@new_device")


@pytest.mark.trio
@customize_fixtures(
    alice_profile=UserProfile.OUTSIDER
)  # Any profile is be allowed to create new devices
async def test_device_create_ok(
    backend, apiv1_backend_sock_factory, apiv1_alice_backend_sock, alice, alice_nd
):
    now = pendulum.now()
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=alice_nd.device_id,
        device_label=None,
        verify_key=alice_nd.verify_key,
    ).dump_and_sign(alice.signing_key)

    with backend.event_bus.listen() as spy:
        rep = await device_create(
            apiv1_alice_backend_sock,
            device_certificate=device_certificate,
            encrypted_answer=b"<good>",
        )
        assert rep == {"status": "ok"}

        # No guarantees this event occurs before the command's return
        await spy.wait_with_timeout(
            BackendEvent.DEVICE_CREATED,
            {
                "organization_id": alice_nd.organization_id,
                "device_id": alice_nd.device_id,
                "device_certificate": device_certificate,
                "encrypted_answer": b"<good>",
            },
        )

    # Make sure the new device can connect now
    async with apiv1_backend_sock_factory(backend, alice_nd) as sock:
        rep = await ping(sock, ping="Hello world !")
        assert rep == {"status": "ok", "pong": "Hello world !"}

    # Check the resulting data in the backend
    _, backend_device = await backend.user.get_user_with_device(
        alice_nd.organization_id, alice_nd.device_id
    )
    assert backend_device == Device(
        device_id=alice_nd.device_id,
        device_label=None,
        device_certificate=device_certificate,
        redacted_device_certificate=device_certificate,
        device_certifier=alice.device_id,
        created_on=now,
    )


@pytest.mark.trio
async def test_device_create_invalid_certified(apiv1_alice_backend_sock, alice, bob, alice_nd):
    now = pendulum.now()
    device_certificate = DeviceCertificateContent(
        author=bob.device_id,
        timestamp=now,
        device_id=alice_nd.device_id,
        device_label=None,
        verify_key=alice_nd.verify_key,
    ).dump_and_sign(bob.signing_key)

    rep = await device_create(
        apiv1_alice_backend_sock, device_certificate=device_certificate, encrypted_answer=b"<good>"
    )
    assert rep == {
        "status": "invalid_certification",
        "reason": "Invalid certification data (Signature was forged or corrupt).",
    }


@pytest.mark.trio
async def test_device_create_already_exists(apiv1_alice_backend_sock, alice, alice2):
    now = pendulum.now()
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=alice2.device_id,
        device_label=None,
        verify_key=alice2.verify_key,
    ).dump_and_sign(alice.signing_key)

    rep = await device_create(
        apiv1_alice_backend_sock, device_certificate=device_certificate, encrypted_answer=b"<good>"
    )
    assert rep == {
        "status": "already_exists",
        "reason": f"Device `{alice2.device_id}` already exists",
    }


@pytest.mark.trio
async def test_device_create_not_own_user(apiv1_bob_backend_sock, bob, alice_nd):
    now = pendulum.now()
    device_certificate = DeviceCertificateContent(
        author=bob.device_id,
        timestamp=now,
        device_id=alice_nd.device_id,
        device_label=None,
        verify_key=alice_nd.verify_key,
    ).dump_and_sign(bob.signing_key)

    rep = await device_create(
        apiv1_bob_backend_sock, device_certificate=device_certificate, encrypted_answer=b"<good>"
    )
    assert rep == {"status": "bad_user_id", "reason": "Device must be handled by it own user."}


@pytest.mark.trio
async def test_device_create_certify_too_old(apiv1_alice_backend_sock, alice, alice_nd):
    now = pendulum.Pendulum(2000, 1, 1)
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=alice_nd.device_id,
        device_label=None,
        verify_key=alice_nd.verify_key,
    ).dump_and_sign(alice.signing_key)

    with freeze_time(now.add(seconds=INVITATION_VALIDITY + 1)):
        rep = await device_create(
            apiv1_alice_backend_sock,
            device_certificate=device_certificate,
            encrypted_answer=b"<good>",
        )
        assert rep == {
            "status": "invalid_certification",
            "reason": "Invalid timestamp in certification.",
        }


@pytest.mark.trio
async def test_device_create_certificate_with_device_label_not_allowed(
    apiv1_alice_backend_sock, alice, alice_nd
):
    now = pendulum.now()
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=alice_nd.device_id,
        device_label=alice_nd.device_label,
        verify_key=alice_nd.verify_key,
    ).dump_and_sign(alice.signing_key)

    rep = await device_create(
        apiv1_alice_backend_sock, device_certificate=device_certificate, encrypted_answer=b"<good>"
    )
    assert rep == {
        "status": "invalid_data",
        "reason": "Redacted Device certificate must not contain a device_label field.",
    }
