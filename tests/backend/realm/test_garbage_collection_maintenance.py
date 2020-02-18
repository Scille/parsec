# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum, now as pendulum_now

from parsec.api.protocol import RealmRole, MaintenanceType
from parsec.backend.realm import RealmGrantedRole

from tests.common import freeze_time
from tests.backend.test_message import message_get
from tests.backend.conftest import realm_status, realm_start_garbage_collection_maintenance


@pytest.mark.trio
async def test_start_bad_timestamp(backend, alice_backend_sock, realm):
    rep = await realm_start_garbage_collection_maintenance(
        alice_backend_sock, realm, 1, Pendulum(2000, 1, 1), {"alice": b"wathever"}, check_rep=False
    )
    assert rep == {"status": "bad_timestamp", "reason": "Timestamp is out of date."}


@pytest.mark.trio
async def test_start_bad_per_participant_message(
    backend, alice_backend_sock, alice, bob, adam, realm
):

    # Bob used to be part of the realm
    await backend.realm.update_roles(
        alice.organization_id,
        RealmGrantedRole(
            certificate=b"<dummy>",
            realm_id=realm,
            user_id=bob.user_id,
            role=RealmRole.READER,
            granted_by=alice.device_id,
        ),
    )
    await backend.realm.update_roles(
        alice.organization_id,
        RealmGrantedRole(
            certificate=b"<dummy>",
            realm_id=realm,
            user_id=bob.user_id,
            role=None,
            granted_by=alice.device_id,
        ),
    )
    # Adam is still part of the realm, but is revoked
    await backend.realm.update_roles(
        alice.organization_id,
        RealmGrantedRole(
            certificate=b"<dummy>",
            realm_id=realm,
            user_id=adam.user_id,
            role=RealmRole.READER,
            granted_by=alice.device_id,
        ),
    )
    await backend.user.revoke_user(
        alice.organization_id,
        adam.user_id,
        revoked_user_certificate=b"<dummy>",
        revoked_user_certifier=alice.device_id,
    )

    for msg in [
        {},
        {alice.user_id: b"ok", bob.user_id: b"bad"},
        {alice.user_id: b"ok", "zack": b"bad"},
        {alice.user_id: b"ok", adam.user_id: b"bad"},
    ]:
        rep = await realm_start_garbage_collection_maintenance(
            alice_backend_sock, realm, 1, pendulum_now(), msg, check_rep=False
        )
        assert rep == {
            "status": "participants_mismatch",
            "reason": "Realm participants and message recipients mismatch",
        }
    # Finally make sure the reencryption is possible
    await realm_start_garbage_collection_maintenance(
        alice_backend_sock, realm, 1, pendulum_now(), {alice.user_id: b"ok"}
    )


@pytest.mark.trio
async def test_start_send_message_to_participants(
    backend, alice, bob, alice_backend_sock, bob_backend_sock, realm
):
    await backend.realm.update_roles(
        alice.organization_id,
        RealmGrantedRole(
            certificate=b"<dummy>",
            realm_id=realm,
            user_id=bob.user_id,
            role=RealmRole.READER,
            granted_by=alice.device_id,
        ),
    )

    with freeze_time("2000-01-02"):
        await realm_start_garbage_collection_maintenance(
            alice_backend_sock, realm, 1, pendulum_now(), {"alice": b"alice msg", "bob": b"bob msg"}
        )

    # Each participant should have received a message
    for user, sock in ((alice, alice_backend_sock), (bob, bob_backend_sock)):
        rep = await message_get(sock)
        assert rep == {
            "status": "ok",
            "messages": [
                {
                    "count": 1,
                    "body": f"{user.user_id} msg".encode(),
                    "timestamp": Pendulum(2000, 1, 2),
                    "sender": alice.device_id,
                }
            ],
        }


@pytest.mark.trio
async def test_start_reencryption_update_status(alice_backend_sock, alice, realm):
    with freeze_time("2000-01-02"):
        await realm_start_garbage_collection_maintenance(
            alice_backend_sock, realm, 1, pendulum_now(), {"alice": b"foo"}
        )
    rep = await realm_status(alice_backend_sock, realm)
    assert rep == {
        "status": "ok",
        "encryption_revision": 1,
        "in_maintenance": True,
        "maintenance_started_by": alice.device_id,
        "maintenance_started_on": Pendulum(2000, 1, 2),
        "maintenance_type": MaintenanceType.GARBAGE_COLLECTION,
    }


@pytest.mark.trio
async def test_start_already_in_maintenance(backend, alice_backend_sock, realm):
    await realm_start_garbage_collection_maintenance(
        alice_backend_sock, realm, 1, pendulum_now(), {"alice": b"wathever"}
    )
    # Providing good or bad encryption revision shouldn't change anything
    for encryption_revision in (2, 3):
        rep = await realm_start_garbage_collection_maintenance(
            alice_backend_sock, realm, 1, pendulum_now(), {"alice": b"wathever"}, check_rep=False
        )
        assert rep == {"status": "in_maintenance"}


@pytest.mark.trio
async def test_start_check_access_rights(backend, bob_backend_sock, alice, bob, realm):
    # User not part of the realm
    rep = await realm_start_garbage_collection_maintenance(
        bob_backend_sock, realm, 1, pendulum_now(), {"alice": b"wathever"}, check_rep=False
    )
    assert rep == {"status": "not_allowed"}

    # User part of the realm with various role
    for not_allowed_role in (RealmRole.READER, RealmRole.CONTRIBUTOR, RealmRole.MANAGER):
        await backend.realm.update_roles(
            alice.organization_id,
            RealmGrantedRole(
                certificate=b"<dummy>",
                realm_id=realm,
                user_id=bob.user_id,
                role=not_allowed_role,
                granted_by=alice.device_id,
            ),
        )

        rep = await realm_start_garbage_collection_maintenance(
            bob_backend_sock,
            realm,
            1,
            pendulum_now(),
            {"alice": b"foo", "bob": b"bar"},
            check_rep=False,
        )
        assert rep == {"status": "not_allowed"}

    # Finally, just make sure owner can do it
    await backend.realm.update_roles(
        alice.organization_id,
        RealmGrantedRole(
            certificate=b"<dummy>",
            realm_id=realm,
            user_id=bob.user_id,
            role=RealmRole.OWNER,
            granted_by=alice.device_id,
        ),
    )

    rep = await realm_start_garbage_collection_maintenance(
        bob_backend_sock,
        realm,
        1,
        pendulum_now(),
        {"alice": b"foo", "bob": b"bar"},
        check_rep=False,
    )
    assert rep == {"status": "ok"}


@pytest.mark.trio
async def test_start_other_organization(backend, sock_from_other_organization_factory, realm):
    async with sock_from_other_organization_factory(backend) as sock:
        rep = await realm_start_garbage_collection_maintenance(
            sock, realm, 1, pendulum_now(), {"alice": b"foo"}, check_rep=False
        )
    assert rep == {
        "status": "not_found",
        "reason": "Realm `a0000000-0000-0000-0000-000000000000` doesn't exist",
    }
