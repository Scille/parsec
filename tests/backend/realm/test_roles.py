# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from uuid import UUID
from pendulum import Pendulum, now as pendulum_now
from unittest.mock import ANY

from parsec.api.protocol import RealmRole
from parsec.api.data import RealmRoleCertificateContent
from parsec.backend.realm import RealmGrantedRole

from tests.common import freeze_time
from tests.backend.conftest import realm_update_roles, realm_get_role_certificates


NOW = Pendulum(2000, 1, 1)
VLOB_ID = UUID("00000000000000000000000000000001")
REALM_ID = UUID("0000000000000000000000000000000A")


@pytest.mark.trio
async def test_get_roles_not_found(alice_backend_sock):
    rep = await realm_get_role_certificates(alice_backend_sock, REALM_ID)
    assert rep == {
        "status": "not_found",
        "reason": "Realm `00000000-0000-0000-0000-00000000000a` doesn't exist",
    }


async def _realm_get_clear_role_certifs(sock, realm_id):
    rep = await realm_get_role_certificates(sock, realm_id)
    assert rep["status"] == "ok"
    cooked = [RealmRoleCertificateContent.unsecure_load(certif) for certif in rep["certificates"]]
    return [item for item in sorted(cooked, key=lambda x: x.timestamp)]


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


async def _backend_realm_generate_certif_and_update_roles(backend, author, realm_id, user_id, role):
    now = pendulum_now()
    certif = RealmRoleCertificateContent(
        author=author.device_id, timestamp=now, realm_id=realm_id, user_id=user_id, role=role
    ).dump_and_sign(author.signing_key)
    await backend.realm.update_roles(
        author.organization_id,
        RealmGrantedRole(
            certificate=certif,
            realm_id=realm_id,
            user_id=user_id,
            role=role,
            granted_by=author.device_id,
            granted_on=now,
        ),
    )
    return certif


@pytest.mark.trio
async def test_update_roles_not_found(alice, bob, alice_backend_sock):
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, REALM_ID, bob.user_id, RealmRole.MANAGER
    )
    assert rep == {
        "status": "not_found",
        "reason": "Realm `00000000-0000-0000-0000-00000000000a` doesn't exist",
    }


@pytest.mark.trio
async def test_update_roles_bad_user(backend, alice, mallory, alice_backend_sock, realm):
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, mallory.user_id, RealmRole.MANAGER
    )
    assert rep == {"status": "not_found", "reason": "User `mallory` doesn't exist"}


@pytest.mark.trio
async def test_update_roles_on_revoked_user(backend, alice, bob, alice_backend_sock, realm):
    # Bob starts with existing role
    with freeze_time("2000-01-03"):
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, RealmRole.READER
        )
        assert rep == {"status": "ok"}

    # Now revoke Bob
    await backend.user.revoke_user(
        organization_id=alice.organization_id,
        user_id=bob.user_id,
        revoked_user_certificate=b"dummy",
        revoked_user_certifier=alice.device_id,
        revoked_on=Pendulum(2000, 1, 4),
    )

    # Not allowed to change Bob's role...
    with freeze_time("2000-01-05"):
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, RealmRole.MANAGER
        )
        assert rep == {"status": "not_allowed"}

    # ...except to remove it role
    with freeze_time("2000-01-06"):
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, None
        )
        assert rep == {"status": "ok"}

    # Sanity check
    certifs = await _realm_get_clear_role_certifs(alice_backend_sock, realm)
    expected_certifs = [
        RealmRoleCertificateContent(
            author=alice.device_id,
            timestamp=Pendulum(2000, 1, 2),
            realm_id=realm,
            user_id=alice.user_id,
            role=RealmRole.OWNER,
        ),
        RealmRoleCertificateContent(
            author=alice.device_id,
            timestamp=Pendulum(2000, 1, 3),
            realm_id=realm,
            user_id=bob.user_id,
            role=RealmRole.READER,
        ),
        RealmRoleCertificateContent(
            author=alice.device_id,
            timestamp=Pendulum(2000, 1, 6),
            realm_id=realm,
            user_id=bob.user_id,
            role=None,
        ),
    ]
    assert certifs == expected_certifs


@pytest.mark.trio
async def test_update_roles_cannot_modify_self(backend, alice, alice_backend_sock, realm):
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, alice.user_id, RealmRole.MANAGER
    )
    assert rep == {
        "status": "invalid_data",
        "reason": "Realm role certificate cannot be self-signed.",
    }


@pytest.mark.trio
@pytest.mark.parametrize("start_with_existing_role", (False, True))
async def test_remove_role_idempotent(
    backend, alice, bob, alice_backend_sock, realm, start_with_existing_role
):
    if start_with_existing_role:
        with freeze_time("2000-01-03"):
            rep = await _realm_generate_certif_and_update_roles_or_fail(
                alice_backend_sock, alice, realm, bob.user_id, RealmRole.MANAGER
            )
            assert rep == {"status": "ok"}

    with freeze_time("2000-01-04"):
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, None
        )
        if start_with_existing_role:
            assert rep == {"status": "ok"}
        else:
            assert rep == {"status": "already_granted"}

    with freeze_time("2000-01-05"):
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, None
        )
        assert rep == {"status": "already_granted"}

    certifs = await _realm_get_clear_role_certifs(alice_backend_sock, realm)
    expected_certifs = [
        RealmRoleCertificateContent(
            author=alice.device_id,
            timestamp=Pendulum(2000, 1, 2),
            realm_id=realm,
            user_id=alice.user_id,
            role=RealmRole.OWNER,
        )
    ]
    if start_with_existing_role:
        expected_certifs += [
            RealmRoleCertificateContent(
                author=alice.device_id,
                timestamp=Pendulum(2000, 1, 3),
                realm_id=realm,
                user_id=bob.user_id,
                role=RealmRole.MANAGER,
            ),
            RealmRoleCertificateContent(
                author=alice.device_id,
                timestamp=Pendulum(2000, 1, 4),
                realm_id=realm,
                user_id=bob.user_id,
                role=None,
            ),
        ]
    assert certifs == expected_certifs


@pytest.mark.trio
async def test_update_roles_as_owner(
    backend, alice, bob, alice_backend_sock, bob_backend_sock, realm
):
    for role in RealmRole:
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, role
        )
        assert rep == {"status": "ok"}

        roles = await backend.realm.get_current_roles(alice.organization_id, realm)
        assert roles == {alice.user_id: RealmRole.OWNER, bob.user_id: role}

    # Now remove role
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, None
    )
    assert rep == {"status": "ok"}

    rep = await realm_get_role_certificates(bob_backend_sock, realm)
    assert rep["status"] == "not_allowed"


@pytest.mark.trio
async def test_update_roles_as_manager(
    backend_data_binder,
    local_device_factory,
    backend,
    alice,
    bob,
    alice_backend_sock,
    bob_backend_sock,
    realm,
):
    # Vlob realm must have at least one owner, so we need 3 users in total
    # (Zack is owner, Alice is manager and gives role to Bob)
    zack = local_device_factory("zack@dev1")
    await backend_data_binder.bind_device(zack)
    await _backend_realm_generate_certif_and_update_roles(
        backend, alice, realm, zack.user_id, RealmRole.OWNER
    )
    await _backend_realm_generate_certif_and_update_roles(
        backend, zack, realm, alice.user_id, RealmRole.MANAGER
    )

    for role in (RealmRole.CONTRIBUTOR, RealmRole.READER):
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, role
        )
        assert rep == {"status": "ok"}

        roles = await backend.realm.get_current_roles(alice.organization_id, realm)
        assert roles == {
            zack.user_id: RealmRole.OWNER,
            alice.user_id: RealmRole.MANAGER,
            bob.user_id: role,
        }

    # Remove role
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, None
    )
    assert rep == {"status": "ok"}
    rep = await realm_get_role_certificates(bob_backend_sock, realm)
    assert rep["status"] == "not_allowed"

    # Cannot give owner or manager role as manager
    for new_role in (RealmRole.OWNER, RealmRole.MANAGER):
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, new_role
        )
        assert rep == {"status": "not_allowed"}

    # Also cannot change owner or manager role
    for new_role in (RealmRole.OWNER, RealmRole.MANAGER):
        await _backend_realm_generate_certif_and_update_roles(
            backend, zack, realm, bob.user_id, new_role
        )
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, zack.user_id, RealmRole.CONTRIBUTOR
        )
        assert rep == {"status": "not_allowed"}


@pytest.mark.trio
@pytest.mark.parametrize("alice_role", (RealmRole.CONTRIBUTOR, RealmRole.READER))
async def test_role_update_not_allowed(
    backend_data_binder,
    local_device_factory,
    backend,
    alice,
    bob,
    alice_backend_sock,
    realm,
    alice_role,
):
    # Vlob realm must have at least one owner, so we need 3 users in total
    # (Zack is owner, Alice gives role to Bob)
    zack = local_device_factory("zack@dev1")
    await backend_data_binder.bind_device(zack)
    await _backend_realm_generate_certif_and_update_roles(
        backend, alice, realm, zack.user_id, RealmRole.OWNER
    )
    await _backend_realm_generate_certif_and_update_roles(
        backend, zack, realm, alice.user_id, alice_role
    )

    # Cannot give role
    for role in RealmRole:
        rep = await _realm_generate_certif_and_update_roles_or_fail(
            alice_backend_sock, alice, realm, bob.user_id, role
        )
        assert rep == {"status": "not_allowed"}

    # Cannot remove role
    await _backend_realm_generate_certif_and_update_roles(
        backend, zack, realm, bob.user_id, RealmRole.READER
    )
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, None
    )
    assert rep == {"status": "not_allowed"}


@pytest.mark.trio
async def test_remove_role_dont_change_other_realms(
    backend, alice, bob, alice_backend_sock, bob_backend_sock, realm, bob_realm
):
    # Bob is owner of bob_realm and manager of realm
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, RealmRole.MANAGER
    )
    assert rep == {"status": "ok"}

    # Remove Bob from realm
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, None
    )
    assert rep == {"status": "ok"}

    # Bob should still have access to bob_realm
    roles = await backend.realm.get_current_roles(alice.organization_id, bob_realm)
    assert roles == {"bob": RealmRole.OWNER}


@pytest.mark.trio
async def test_role_access_during_maintenance(
    backend, alice, bob, alice_backend_sock, realm, vlobs
):
    await backend.realm.start_reencryption_maintenance(
        alice.organization_id,
        alice.device_id,
        realm,
        2,
        {alice.user_id: b"whatever"},
        Pendulum(2000, 1, 2),
    )

    # Get roles allowed...
    roles = await backend.realm.get_current_roles(alice.organization_id, realm)
    assert roles == {"alice": RealmRole.OWNER}

    rep = await realm_get_role_certificates(alice_backend_sock, realm)
    assert rep == {"status": "ok", "certificates": [ANY]}

    # ...buit not update role
    rep = await _realm_generate_certif_and_update_roles_or_fail(
        alice_backend_sock, alice, realm, bob.user_id, RealmRole.MANAGER
    )
    assert rep == {"status": "in_maintenance"}


@pytest.mark.trio
async def test_get_role_certificates_partial(backend, alice, bob, adam, bob_backend_sock, realm):
    # Realm is created on 2000-01-02

    with freeze_time("2000-01-03"):
        c3 = await _backend_realm_generate_certif_and_update_roles(
            backend, alice, realm, bob.user_id, RealmRole.OWNER
        )

    with freeze_time("2000-01-04"):
        c4 = await _backend_realm_generate_certif_and_update_roles(
            backend, bob, realm, adam.user_id, RealmRole.MANAGER
        )

    with freeze_time("2000-01-05"):
        c5 = await _backend_realm_generate_certif_and_update_roles(
            backend, bob, realm, alice.user_id, RealmRole.READER
        )

    with freeze_time("2000-01-06"):
        c6 = await _backend_realm_generate_certif_and_update_roles(
            backend, bob, realm, alice.user_id, None
        )

    rep = await realm_get_role_certificates(bob_backend_sock, realm)
    assert rep == {"status": "ok", "certificates": [ANY, c3, c4, c5, c6]}

    rep = await realm_get_role_certificates(bob_backend_sock, realm, Pendulum(2000, 1, 3))
    assert rep == {"status": "ok", "certificates": [c4, c5, c6]}

    rep = await realm_get_role_certificates(bob_backend_sock, realm, Pendulum(2000, 1, 5))
    assert rep == {"status": "ok", "certificates": [c6]}

    rep = await realm_get_role_certificates(bob_backend_sock, realm, Pendulum(2000, 1, 7))
    assert rep == {"status": "ok", "certificates": []}

    rep = await realm_get_role_certificates(bob_backend_sock, realm, Pendulum(2000, 1, 6))
    assert rep == {"status": "ok", "certificates": []}


@pytest.mark.trio
async def test_get_role_certificates_no_longer_allowed(
    backend, alice, bob, alice_backend_sock, realm
):
    # Realm is created on 2000-01-02

    with freeze_time("2000-01-03"):
        await _backend_realm_generate_certif_and_update_roles(
            backend, alice, realm, bob.user_id, RealmRole.OWNER
        )

    with freeze_time("2000-01-04"):
        await _backend_realm_generate_certif_and_update_roles(
            backend, bob, realm, alice.user_id, None
        )

    rep = await realm_get_role_certificates(alice_backend_sock, realm)
    assert rep == {"status": "not_allowed"}
