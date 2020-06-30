# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
import pytest

from parsec.api.data import DeviceCertificateContent, UserCertificateContent, UserProfile
from parsec.api.protocol import apiv1_user_create_serializer
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import INVITATION_VALIDITY, Device, User
from tests.backend.common import user_get
from tests.common import freeze_time


async def user_create(sock, **kwargs):
    await sock.send(apiv1_user_create_serializer.req_dumps({"cmd": "user_create", **kwargs}))
    raw_rep = await sock.recv()
    rep = apiv1_user_create_serializer.rep_loads(raw_rep)
    return rep


@pytest.mark.trio
@pytest.mark.parametrize("profile", UserProfile)
async def test_user_create_ok(backend, apiv1_backend_sock_factory, alice, mallory, profile):
    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=alice.device_id,
        timestamp=now,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=profile,
    ).dump_and_sign(alice.signing_key)
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=mallory.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(alice.signing_key)

    with backend.event_bus.listen() as spy:
        async with apiv1_backend_sock_factory(backend, alice) as sock:
            rep = await user_create(
                sock, user_certificate=user_certificate, device_certificate=device_certificate
            )
        assert rep == {"status": "ok"}

        # No guarantees this event occurs before the command's return
        await spy.wait_with_timeout(
            BackendEvent.USER_CREATED,
            {
                "organization_id": alice.organization_id,
                "user_id": mallory.user_id,
                "first_device_id": mallory.device_id,
                "user_certificate": user_certificate,
                "first_device_certificate": device_certificate,
            },
        )

    # Make sure mallory can connect now
    async with apiv1_backend_sock_factory(backend, mallory) as sock:
        rep = await user_get(sock, user_id=mallory.user_id)
        assert rep["status"] == "ok"

    # Check the resulting data in the backend
    backend_user, backend_device = await backend.user.get_user_with_device(
        mallory.organization_id, mallory.device_id
    )
    assert backend_user == User(
        user_id=mallory.user_id,
        human_handle=None,
        profile=profile,
        user_certificate=user_certificate,
        redacted_user_certificate=user_certificate,
        user_certifier=alice.device_id,
        created_on=now,
    )
    assert backend_device == Device(
        device_id=mallory.device_id,
        device_label=None,
        device_certificate=device_certificate,
        redacted_device_certificate=device_certificate,
        device_certifier=alice.device_id,
        created_on=now,
    )


@pytest.mark.trio
async def test_user_create_invalid_certificate(
    backend, apiv1_backend_sock_factory, alice, bob, mallory
):
    now = pendulum.now()
    good_user_certificate = UserCertificateContent(
        author=alice.device_id,
        timestamp=now,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(alice.signing_key)
    good_device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=mallory.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(alice.signing_key)
    bad_user_certificate = UserCertificateContent(
        author=bob.device_id,
        timestamp=now,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(bob.signing_key)
    bad_device_certificate = DeviceCertificateContent(
        author=bob.device_id,
        timestamp=now,
        device_id=mallory.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(bob.signing_key)

    async with apiv1_backend_sock_factory(backend, alice) as sock:
        for cu, cd in [
            (good_user_certificate, bad_device_certificate),
            (bad_user_certificate, good_device_certificate),
            (bad_user_certificate, bad_device_certificate),
        ]:
            rep = await user_create(sock, user_certificate=cu, device_certificate=cd)
            assert rep == {
                "status": "invalid_certification",
                "reason": "Invalid certification data (Signature was forged or corrupt).",
            }


@pytest.mark.trio
async def test_user_create_not_matching_user_device(
    backend, apiv1_backend_sock_factory, alice, bob, mallory
):
    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=alice.device_id,
        timestamp=now,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(alice.signing_key)
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=bob.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(alice.signing_key)

    async with apiv1_backend_sock_factory(backend, alice) as sock:
        rep = await user_create(
            sock, user_certificate=user_certificate, device_certificate=device_certificate
        )
    assert rep == {
        "status": "invalid_data",
        "reason": "Device and User must have the same user ID.",
    }


@pytest.mark.trio
async def test_user_create_already_exists(backend, apiv1_backend_sock_factory, alice, bob):
    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=alice.device_id,
        timestamp=now,
        user_id=bob.user_id,
        human_handle=None,
        public_key=bob.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(alice.signing_key)
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=bob.device_id,
        device_label=None,
        verify_key=bob.verify_key,
    ).dump_and_sign(alice.signing_key)

    async with apiv1_backend_sock_factory(backend, alice) as sock:
        rep = await user_create(
            sock, user_certificate=user_certificate, device_certificate=device_certificate
        )
    assert rep == {"status": "already_exists", "reason": f"User `{bob.user_id}` already exists"}


@pytest.mark.trio
async def test_user_create_human_handle_not_allowed(
    backend, apiv1_backend_sock_factory, alice, mallory
):
    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=alice.device_id,
        timestamp=now,
        user_id=mallory.user_id,
        human_handle=mallory.human_handle,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(alice.signing_key)
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=mallory.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(alice.signing_key)

    async with apiv1_backend_sock_factory(backend, alice) as sock:
        rep = await user_create(
            sock, user_certificate=user_certificate, device_certificate=device_certificate
        )
    assert rep == {
        "status": "invalid_data",
        "reason": "Redacted User certificate must not contain a human_handle field.",
    }


@pytest.mark.trio
async def test_user_create_device_label_not_allowed(
    backend, apiv1_backend_sock_factory, alice, mallory
):
    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=alice.device_id,
        timestamp=now,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(alice.signing_key)
    device_certificate = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=now,
        device_id=mallory.device_id,
        device_label=mallory.device_label,
        verify_key=mallory.verify_key,
    ).dump_and_sign(alice.signing_key)

    async with apiv1_backend_sock_factory(backend, alice) as sock:
        rep = await user_create(
            sock, user_certificate=user_certificate, device_certificate=device_certificate
        )
    assert rep == {
        "status": "invalid_data",
        "reason": "Redacted Device certificate must not contain a device_label field.",
    }


@pytest.mark.trio
async def test_user_create_not_matching_certified_on(
    backend, apiv1_backend_sock_factory, alice, mallory
):
    date1 = pendulum.Pendulum(2000, 1, 1)
    date2 = date1.add(seconds=1)
    cu = UserCertificateContent(
        author=alice.device_id,
        timestamp=date1,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(alice.signing_key)
    cd = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=date2,
        device_id=mallory.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(alice.signing_key)
    with freeze_time(date1):
        async with apiv1_backend_sock_factory(backend, alice) as sock:
            rep = await user_create(sock, user_certificate=cu, device_certificate=cd)
        assert rep == {
            "status": "invalid_data",
            "reason": "Device and User certificates must have the same timestamp.",
        }


@pytest.mark.trio
async def test_user_create_certify_too_old(backend, apiv1_backend_sock_factory, alice, mallory):
    too_old = pendulum.Pendulum(2000, 1, 1)
    now = too_old.add(seconds=INVITATION_VALIDITY + 1)
    cu = UserCertificateContent(
        author=alice.device_id,
        timestamp=too_old,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(alice.signing_key)
    cd = DeviceCertificateContent(
        author=alice.device_id,
        timestamp=too_old,
        device_id=mallory.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(alice.signing_key)

    with freeze_time(now):
        async with apiv1_backend_sock_factory(backend, alice) as sock:
            rep = await user_create(sock, user_certificate=cu, device_certificate=cd)
        assert rep == {
            "status": "invalid_certification",
            "reason": "Invalid timestamp in certificate.",
        }


@pytest.mark.trio
async def test_user_create_author_not_admin(backend, apiv1_backend_sock_factory, bob, mallory):
    # Unlike alice, bob is not admin
    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=bob.device_id,
        timestamp=now,
        user_id=mallory.user_id,
        human_handle=None,
        public_key=mallory.public_key,
        profile=UserProfile.STANDARD,
    ).dump_and_sign(bob.signing_key)
    device_certificate = DeviceCertificateContent(
        author=bob.device_id,
        timestamp=now,
        device_id=mallory.device_id,
        device_label=None,
        verify_key=mallory.verify_key,
    ).dump_and_sign(bob.signing_key)

    async with apiv1_backend_sock_factory(backend, bob) as sock:
        rep = await user_create(
            sock, user_certificate=user_certificate, device_certificate=device_certificate
        )
    assert rep == {"status": "not_allowed", "reason": "User `bob` is not admin"}
