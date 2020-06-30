# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

import pendulum
import pytest

from parsec.api.data import RealmRoleCertificateContent, UserProfile
from parsec.api.protocol import RealmRole
from parsec.backend.backend_events import BackendEvent
from parsec.utils import TIMESTAMP_MAX_DT
from tests.backend.common import realm_create
from tests.backend.test_events import events_subscribe
from tests.common import customize_fixtures, freeze_time


@pytest.mark.trio
async def test_create_ok(backend, alice, alice_backend_sock):
    await events_subscribe(alice_backend_sock)

    realm_id = UUID("C0000000000000000000000000000000")
    certif = RealmRoleCertificateContent.build_realm_root_certif(
        author=alice.device_id, timestamp=pendulum.now(), realm_id=realm_id
    ).dump_and_sign(alice.signing_key)
    with backend.event_bus.listen() as spy:
        rep = await realm_create(alice_backend_sock, certif)
        assert rep == {"status": "ok"}
        await spy.wait_with_timeout(BackendEvent.REALM_ROLES_UPDATED)


@pytest.mark.trio
async def test_create_invalid_certif(backend, alice, bob, alice_backend_sock):
    realm_id = UUID("C0000000000000000000000000000000")
    certif = RealmRoleCertificateContent.build_realm_root_certif(
        author=bob.device_id, timestamp=pendulum.now(), realm_id=realm_id
    ).dump_and_sign(bob.signing_key)
    rep = await realm_create(alice_backend_sock, certif)
    assert rep == {
        "status": "invalid_certification",
        "reason": "Invalid certification data (Signature was forged or corrupt).",
    }


@pytest.mark.trio
async def test_create_certif_not_self_signed(backend, alice, bob, alice_backend_sock):
    realm_id = UUID("C0000000000000000000000000000000")
    certif = RealmRoleCertificateContent(
        author=alice.device_id,
        timestamp=pendulum.now(),
        realm_id=realm_id,
        user_id=bob.user_id,
        role=RealmRole.OWNER,
    ).dump_and_sign(alice.signing_key)
    rep = await realm_create(alice_backend_sock, certif)
    assert rep == {
        "status": "invalid_data",
        "reason": "Initial realm role certificate must be self-signed.",
    }


@pytest.mark.trio
async def test_create_certif_role_not_owner(backend, alice, bob, alice_backend_sock):
    realm_id = UUID("C0000000000000000000000000000000")
    certif = RealmRoleCertificateContent(
        author=alice.device_id,
        timestamp=pendulum.now(),
        realm_id=realm_id,
        user_id=alice.user_id,
        role=RealmRole.MANAGER,
    ).dump_and_sign(alice.signing_key)
    rep = await realm_create(alice_backend_sock, certif)
    assert rep == {
        "status": "invalid_data",
        "reason": "Initial realm role certificate must set OWNER role.",
    }


@pytest.mark.trio
async def test_create_certif_too_old(backend, alice, alice_backend_sock):
    realm_id = UUID("C0000000000000000000000000000000")
    now = pendulum.now()
    certif = RealmRoleCertificateContent.build_realm_root_certif(
        author=alice.device_id, timestamp=now, realm_id=realm_id
    ).dump_and_sign(alice.signing_key)
    with freeze_time(now.add(seconds=TIMESTAMP_MAX_DT)):
        rep = await realm_create(alice_backend_sock, certif)
    assert rep == {
        "status": "invalid_certification",
        "reason": "Invalid timestamp in certification.",
    }


@pytest.mark.trio
async def test_create_realm_already_exists(backend, alice, alice_backend_sock, realm):
    certif = RealmRoleCertificateContent.build_realm_root_certif(
        author=alice.device_id, timestamp=pendulum.now(), realm_id=realm
    ).dump_and_sign(alice.signing_key)
    rep = await realm_create(alice_backend_sock, certif)
    assert rep == {"status": "already_exists"}


@pytest.mark.trio
@customize_fixtures(alice_profile=UserProfile.OUTSIDER)
async def test_realm_create_not_allowed_for_outsider(backend, alice, alice_backend_sock):
    realm_id = UUID("C0000000000000000000000000000000")
    certif = RealmRoleCertificateContent.build_realm_root_certif(
        author=alice.device_id, timestamp=pendulum.now(), realm_id=realm_id
    ).dump_and_sign(alice.signing_key)
    rep = await realm_create(alice_backend_sock, certif, check_rep=False)
    assert rep == {"status": "not_allowed", "reason": "Outsider user cannot create realm"}
