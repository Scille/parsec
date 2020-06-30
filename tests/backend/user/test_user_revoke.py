# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data import RevokedUserCertificateContent, UserProfile
from parsec.api.protocol import HandshakeRevokedDevice
from parsec.api.transport import TransportError
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import INVITATION_VALIDITY
from tests.backend.common import ping, user_revoke
from tests.common import freeze_time


@pytest.mark.trio
async def test_backend_close_on_user_revoke(
    backend, alice_backend_sock, backend_sock_factory, bob, alice
):
    now = pendulum_now()
    bob_revocation = RevokedUserCertificateContent(
        author=alice.device_id, timestamp=now, user_id=bob.user_id
    ).dump_and_sign(alice.signing_key)

    async with backend_sock_factory(
        backend, bob, freeze_on_transport_error=False
    ) as bob_backend_sock:
        with backend.event_bus.listen() as spy:
            rep = await user_revoke(alice_backend_sock, revoked_user_certificate=bob_revocation)
            assert rep == {"status": "ok"}
            await spy.wait_with_timeout(
                BackendEvent.USER_REVOKED,
                {"organization_id": bob.organization_id, "user_id": bob.user_id},
            )
            # `user.revoked` event schedules connection cancellation, so wait
            # for things to settle down to make sure the cancellation is done
            await trio.testing.wait_all_tasks_blocked()
        # Bob cannot send new command
        with pytest.raises(TransportError):
            await ping(bob_backend_sock)


@pytest.mark.trio
async def test_user_revoke_ok(backend, backend_sock_factory, adam_backend_sock, alice, adam):
    now = pendulum_now()
    alice_revocation = RevokedUserCertificateContent(
        author=adam.device_id, timestamp=now, user_id=alice.user_id
    ).dump_and_sign(adam.signing_key)

    with backend.event_bus.listen() as spy:
        rep = await user_revoke(adam_backend_sock, revoked_user_certificate=alice_revocation)
        assert rep == {"status": "ok"}
        await spy.wait_with_timeout(
            BackendEvent.USER_REVOKED,
            {"organization_id": alice.organization_id, "user_id": alice.user_id},
        )

    # Alice cannot connect from now on...
    with pytest.raises(HandshakeRevokedDevice):
        async with backend_sock_factory(backend, alice):
            pass


@pytest.mark.trio
async def test_user_revoke_not_admin(backend, backend_sock_factory, bob_backend_sock, alice, bob):
    now = pendulum_now()
    alice_revocation = RevokedUserCertificateContent(
        author=bob.device_id, timestamp=now, user_id=alice.user_id
    ).dump_and_sign(bob.signing_key)

    rep = await user_revoke(bob_backend_sock, revoked_user_certificate=alice_revocation)
    assert rep == {"status": "not_allowed", "reason": "User `bob` is not admin"}


@pytest.mark.trio
async def test_cannot_self_revoke(backend, backend_sock_factory, alice_backend_sock, alice):
    now = pendulum_now()
    alice_revocation = RevokedUserCertificateContent(
        author=alice.device_id, timestamp=now, user_id=alice.user_id
    ).dump_and_sign(alice.signing_key)

    rep = await user_revoke(alice_backend_sock, revoked_user_certificate=alice_revocation)
    assert rep == {"status": "not_allowed", "reason": "Cannot do self-revocation"}


@pytest.mark.trio
async def test_user_revoke_unknown(backend, alice_backend_sock, alice, mallory):
    revoked_user_certificate = RevokedUserCertificateContent(
        author=alice.device_id, timestamp=pendulum_now(), user_id=mallory.user_id
    ).dump_and_sign(alice.signing_key)

    rep = await user_revoke(alice_backend_sock, revoked_user_certificate=revoked_user_certificate)
    assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_user_revoke_already_revoked(backend, alice_backend_sock, bob, alice):
    now = pendulum_now()
    bob_revocation = RevokedUserCertificateContent(
        author=alice.device_id, timestamp=now, user_id=bob.user_id
    ).dump_and_sign(alice.signing_key)

    rep = await user_revoke(alice_backend_sock, revoked_user_certificate=bob_revocation)
    assert rep["status"] == "ok"

    rep = await user_revoke(alice_backend_sock, revoked_user_certificate=bob_revocation)
    assert rep == {"status": "already_revoked", "reason": f"User `{bob.user_id}` already revoked"}


@pytest.mark.trio
async def test_user_revoke_invalid_certified(backend, alice_backend_sock, alice2, bob):
    revoked_user_certificate = RevokedUserCertificateContent(
        author=alice2.device_id, timestamp=pendulum_now(), user_id=bob.user_id
    ).dump_and_sign(alice2.signing_key)

    rep = await user_revoke(alice_backend_sock, revoked_user_certificate=revoked_user_certificate)
    assert rep == {
        "status": "invalid_certification",
        "reason": "Invalid certification data (Signature was forged or corrupt).",
    }


@pytest.mark.trio
async def test_user_revoke_certify_too_old(backend, alice_backend_sock, alice, bob):
    now = Pendulum(2000, 1, 1)
    revoked_user_certificate = RevokedUserCertificateContent(
        author=alice.device_id, timestamp=now, user_id=bob.user_id
    ).dump_and_sign(alice.signing_key)

    with freeze_time(now.add(seconds=INVITATION_VALIDITY + 1)):
        rep = await user_revoke(
            alice_backend_sock, revoked_user_certificate=revoked_user_certificate
        )
        assert rep == {
            "status": "invalid_certification",
            "reason": "Invalid timestamp in certification.",
        }


@pytest.mark.trio
async def test_user_revoke_other_organization(
    sock_from_other_organization_factory, backend_sock_factory, backend, alice, bob
):
    # Organizations should be isolated even for organization admins
    async with sock_from_other_organization_factory(
        backend, mimick=alice.device_id, profile=UserProfile.ADMIN
    ) as sock:

        revocation = RevokedUserCertificateContent(
            author=sock.device.device_id, timestamp=pendulum_now(), user_id=bob.user_id
        ).dump_and_sign(sock.device.signing_key)

        rep = await user_revoke(sock, revoked_user_certificate=revocation)
        assert rep == {"status": "not_found"}

    # Make sure bob still works
    async with backend_sock_factory(backend, bob):
        pass
