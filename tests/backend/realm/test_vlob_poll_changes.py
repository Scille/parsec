# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

import pytest
from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data import RealmRoleCertificateContent
from parsec.api.protocol import RealmRole
from tests.backend.common import realm_update_roles, vlob_poll_changes, vlob_update

NOW = Pendulum(2000, 1, 1)
VLOB_ID = UUID("00000000000000000000000000000001")
OTHER_VLOB_ID = UUID("00000000000000000000000000000002")
YET_ANOTHER_VLOB_ID = UUID("00000000000000000000000000000003")
UNKNOWN_REALM_ID = UUID("0000000000000000000000000000000F")


async def _realm_generate_certif_and_update_roles_or_fail(
    backend_sock, author, realm_id, user_id, role
):
    certif = RealmRoleCertificateContent(
        author=author.device_id,
        timestamp=pendulum_now(),
        realm_id=realm_id,
        user_id=user_id,
        role=role,
    ).dump_and_sign(author.signing_key)
    return await realm_update_roles(backend_sock, certif, check_rep=False)


@pytest.mark.trio
async def test_realm_updated_by_vlob(backend, alice, alice_backend_sock, realm):
    await backend.vlob.create(
        organization_id=alice.organization_id,
        author=alice.device_id,
        realm_id=realm,
        encryption_revision=1,
        vlob_id=VLOB_ID,
        timestamp=NOW,
        blob=b"v1",
    )
    await backend.vlob.update(
        organization_id=alice.organization_id,
        author=alice.device_id,
        encryption_revision=1,
        vlob_id=VLOB_ID,
        version=2,
        timestamp=NOW,
        blob=b"v2",
    )

    for last_checkpoint in (0, 1):
        rep = await vlob_poll_changes(alice_backend_sock, realm, last_checkpoint)
        assert rep == {"status": "ok", "current_checkpoint": 2, "changes": {VLOB_ID: 2}}


@pytest.mark.trio
async def test_vlob_poll_changes_checkpoint_up_to_date(backend, alice, alice_backend_sock, realm):
    await backend.vlob.create(
        organization_id=alice.organization_id,
        author=alice.device_id,
        realm_id=realm,
        encryption_revision=1,
        vlob_id=VLOB_ID,
        timestamp=NOW,
        blob=b"v1",
    )
    await backend.vlob.update(
        organization_id=alice.organization_id,
        author=alice.device_id,
        encryption_revision=1,
        vlob_id=VLOB_ID,
        version=2,
        timestamp=NOW,
        blob=b"v2",
    )

    rep = await vlob_poll_changes(alice_backend_sock, realm, 2)
    assert rep == {"status": "ok", "current_checkpoint": 2, "changes": {}}


@pytest.mark.trio
async def test_vlob_poll_changes_not_found(alice_backend_sock):
    rep = await vlob_poll_changes(alice_backend_sock, UNKNOWN_REALM_ID, 0)
    assert rep == {
        "status": "not_found",
        "reason": "Realm `00000000-0000-0000-0000-00000000000f` doesn't exist",
    }


@pytest.mark.trio
async def test_vlob_poll_changes(backend, alice, bob, alice_backend_sock, bob_backend_sock, realm):
    await backend.vlob.create(
        organization_id=alice.organization_id,
        author=alice.device_id,
        realm_id=realm,
        encryption_revision=1,
        vlob_id=VLOB_ID,
        timestamp=NOW,
        blob=b"v1",
    )

    # At first only Alice is allowed

    rep = await vlob_poll_changes(bob_backend_sock, realm, 2)
    assert rep == {"status": "not_allowed"}

    # Add Bob with read&write rights

    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, RealmRole.CONTRIBUTOR
    )
    assert rep == {"status": "ok"}

    rep = await vlob_update(bob_backend_sock, VLOB_ID, 2, b"v2")
    assert rep == {"status": "ok"}

    rep = await vlob_poll_changes(bob_backend_sock, realm, 1)
    assert rep == {"status": "ok", "current_checkpoint": 2, "changes": {VLOB_ID: 2}}

    # Change Bob with read only right

    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, RealmRole.READER
    )
    assert rep == {"status": "ok"}

    rep = await vlob_update(bob_backend_sock, VLOB_ID, 3, b"v3", check_rep=False)
    assert rep == {"status": "not_allowed"}

    rep = await vlob_poll_changes(bob_backend_sock, realm, 1)
    assert rep == {"status": "ok", "current_checkpoint": 2, "changes": {VLOB_ID: 2}}

    # Finally remove all rights from Bob

    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, None
    )
    assert rep == {"status": "ok"}

    rep = await vlob_poll_changes(bob_backend_sock, realm, 2)
    assert rep == {"status": "not_allowed"}

    rep = await vlob_update(bob_backend_sock, VLOB_ID, 3, b"v3", check_rep=False)
    assert rep == {"status": "not_allowed"}


@pytest.mark.trio
async def test_vlob_poll_changes_during_maintenance(backend, alice, alice_backend_sock, realm):
    await backend.realm.start_reencryption_maintenance(
        alice.organization_id,
        alice.device_id,
        realm,
        2,
        {alice.user_id: b"whatever"},
        Pendulum(2000, 1, 2),
    )

    # Realm under maintenance are simply skipped
    rep = await vlob_poll_changes(alice_backend_sock, realm, 1)
    assert rep == {"status": "in_maintenance"}
