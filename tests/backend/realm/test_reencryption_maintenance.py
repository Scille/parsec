# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from pendulum import Pendulum, now as pendulum_now

from parsec.api.protocol import RealmRole, MaintenanceType
from parsec.backend.realm import RealmGrantedRole

from tests.common import freeze_time
from tests.backend.test_message import message_get
from tests.backend.conftest import (
    realm_status,
    realm_start_reencryption_maintenance,
    realm_finish_reencryption_maintenance,
    vlob_read,
    vlob_maintenance_get_reencryption_batch,
    vlob_maintenance_save_reencryption_batch,
)
from tests.backend.test_events import events_subscribe, events_listen_nowait


@pytest.mark.trio
async def test_start_bad_encryption_revision(backend, alice_backend_sock, realm):
    rep = await realm_start_reencryption_maintenance(
        alice_backend_sock, realm, 42, pendulum_now(), {"alice": b"wathever"}, check_rep=False
    )
    assert rep == {"status": "bad_encryption_revision"}


@pytest.mark.trio
async def test_start_bad_timestamp(backend, alice_backend_sock, realm):
    rep = await realm_start_reencryption_maintenance(
        alice_backend_sock, realm, 2, Pendulum(2000, 1, 1), {"alice": b"wathever"}, check_rep=False
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
        rep = await realm_start_reencryption_maintenance(
            alice_backend_sock, realm, 2, pendulum_now(), msg, check_rep=False
        )
        assert rep == {
            "status": "participants_mismatch",
            "reason": "Realm participants and message recipients mismatch",
        }

    # Finally make sure the reencryption is possible
    await realm_start_reencryption_maintenance(
        alice_backend_sock, realm, 2, pendulum_now(), {alice.user_id: b"ok"}
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
        await realm_start_reencryption_maintenance(
            alice_backend_sock, realm, 2, pendulum_now(), {"alice": b"alice msg", "bob": b"bob msg"}
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
        await realm_start_reencryption_maintenance(
            alice_backend_sock, realm, 2, pendulum_now(), {"alice": b"foo"}
        )
    rep = await realm_status(alice_backend_sock, realm)
    assert rep == {
        "status": "ok",
        "encryption_revision": 2,
        "in_maintenance": True,
        "maintenance_started_by": alice.device_id,
        "maintenance_started_on": Pendulum(2000, 1, 2),
        "maintenance_type": MaintenanceType.REENCRYPTION,
    }


@pytest.mark.trio
async def test_start_already_in_maintenance(backend, alice_backend_sock, realm):
    await realm_start_reencryption_maintenance(
        alice_backend_sock, realm, 2, pendulum_now(), {"alice": b"wathever"}
    )
    # Providing good or bad encryption revision shouldn't change anything
    for encryption_revision in (2, 3):
        rep = await realm_start_reencryption_maintenance(
            alice_backend_sock,
            realm,
            encryption_revision,
            pendulum_now(),
            {"alice": b"wathever"},
            check_rep=False,
        )
        assert rep == {"status": "in_maintenance"}


@pytest.mark.trio
async def test_start_check_access_rights(backend, bob_backend_sock, alice, bob, realm):
    # User not part of the realm
    rep = await realm_start_reencryption_maintenance(
        bob_backend_sock, realm, 2, pendulum_now(), {"alice": b"wathever"}, check_rep=False
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

        rep = await realm_start_reencryption_maintenance(
            bob_backend_sock,
            realm,
            2,
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

    rep = await realm_start_reencryption_maintenance(
        bob_backend_sock,
        realm,
        2,
        pendulum_now(),
        {"alice": b"foo", "bob": b"bar"},
        check_rep=False,
    )
    assert rep == {"status": "ok"}


@pytest.mark.trio
async def test_start_other_organization(backend, sock_from_other_organization_factory, realm):
    async with sock_from_other_organization_factory(backend) as sock:
        rep = await realm_start_reencryption_maintenance(
            sock, realm, 2, pendulum_now(), {"alice": b"foo"}, check_rep=False
        )
    assert rep == {
        "status": "not_found",
        "reason": "Realm `a0000000-0000-0000-0000-000000000000` doesn't exist",
    }


@pytest.mark.trio
async def test_finish_not_in_maintenance(alice_backend_sock, realm):
    for encryption_revision in (2, 3):
        rep = await realm_finish_reencryption_maintenance(
            alice_backend_sock, realm, encryption_revision, check_rep=False
        )
        assert rep == {
            "status": "not_in_maintenance",
            "reason": "Realm `a0000000-0000-0000-0000-000000000000` not under maintenance",
        }


@pytest.mark.trio
async def test_finish_while_reencryption_not_done(alice_backend_sock, realm, vlobs):
    await realm_start_reencryption_maintenance(
        alice_backend_sock, realm, 2, pendulum_now(), {"alice": b"wathever"}
    )
    rep = await realm_finish_reencryption_maintenance(alice_backend_sock, realm, 2, check_rep=False)
    assert rep == {"status": "maintenance_error", "reason": "Reencryption operations are not over"}

    # Also try with part of the job done
    rep = await vlob_maintenance_get_reencryption_batch(alice_backend_sock, realm, 2, size=2)
    assert rep["status"] == "ok"
    assert len(rep["batch"]) == 2
    for entry in rep["batch"]:
        entry["blob"] = f"{entry['vlob_id']}::{entry['version']} reencrypted".encode()
    await vlob_maintenance_save_reencryption_batch(alice_backend_sock, realm, 2, rep["batch"])

    rep = await realm_finish_reencryption_maintenance(alice_backend_sock, realm, 2, check_rep=False)
    assert rep == {"status": "maintenance_error", "reason": "Reencryption operations are not over"}


@pytest.mark.trio
async def test_reencrypt_and_finish_check_access_rights(
    backend, alice_backend_sock, bob_backend_sock, alice, bob, realm, vlobs
):
    encryption_revision = 1
    start_reencryption_msgs = {"alice": b"foo"}

    # Changing realm roles is not possible during maintenance,
    # hence those helpers to easily jump in/out of maintenance

    async def _ready_to_finish():
        nonlocal encryption_revision
        encryption_revision += 1
        await realm_start_reencryption_maintenance(
            alice_backend_sock, realm, encryption_revision, pendulum_now(), start_reencryption_msgs
        )
        updated_batch = [
            {
                "vlob_id": vlob_id,
                "version": version,
                "blob": f"{vlob_id}::{version}::{encryption_revision}".encode(),
            }
            for vlob_id, version in {(vlobs[0], 1), (vlobs[0], 2), (vlobs[1], 1)}
        ]
        await vlob_maintenance_save_reencryption_batch(
            alice_backend_sock, realm, encryption_revision, updated_batch
        )

    async def _finish():
        await realm_finish_reencryption_maintenance(alice_backend_sock, realm, encryption_revision)

    async def _assert_bob_maintenance_access(allowed):
        if allowed:
            expected_status = "ok"
        else:
            expected_status = "not_allowed"
        rep = await vlob_maintenance_save_reencryption_batch(
            bob_backend_sock, realm, encryption_revision, [], check_rep=False
        )
        assert rep["status"] == expected_status
        rep = await realm_finish_reencryption_maintenance(
            bob_backend_sock, realm, encryption_revision, check_rep=False
        )
        assert rep["status"] == expected_status

    # User not part of the realm
    await _ready_to_finish()
    await _assert_bob_maintenance_access(allowed=False)
    await _finish()

    # User part of the realm with various role
    start_reencryption_msgs["bob"] = b"bar"
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
        await _ready_to_finish()
        await _assert_bob_maintenance_access(allowed=False)
        await _finish()

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
    await _ready_to_finish()
    await _assert_bob_maintenance_access(allowed=True)


@pytest.mark.trio
async def test_reencryption_batch_not_during_maintenance(alice_backend_sock, realm):
    rep = await vlob_maintenance_get_reencryption_batch(alice_backend_sock, realm, 1)
    assert rep == {
        "status": "not_in_maintenance",
        "reason": "Realm `a0000000-0000-0000-0000-000000000000` not under maintenance",
    }

    rep = await vlob_maintenance_save_reencryption_batch(
        alice_backend_sock, realm, 1, [], check_rep=False
    )
    assert rep == {
        "status": "not_in_maintenance",
        "reason": "Realm `a0000000-0000-0000-0000-000000000000` not under maintenance",
    }

    rep = await realm_finish_reencryption_maintenance(alice_backend_sock, realm, 1, check_rep=False)
    assert rep == {
        "status": "not_in_maintenance",
        "reason": "Realm `a0000000-0000-0000-0000-000000000000` not under maintenance",
    }


@pytest.mark.trio
async def test_reencryption_batch_bad_revisison(alice_backend_sock, realm):
    await realm_start_reencryption_maintenance(
        alice_backend_sock, realm, 2, pendulum_now(), {"alice": b"foo"}
    )

    rep = await vlob_maintenance_get_reencryption_batch(alice_backend_sock, realm, 1)
    assert rep == {"status": "bad_encryption_revision"}

    rep = await realm_finish_reencryption_maintenance(alice_backend_sock, realm, 1, check_rep=False)
    assert rep == {"status": "bad_encryption_revision"}


@pytest.mark.trio
async def test_reencryption(alice, alice_backend_sock, realm, vlobs, vlob_atoms):
    with freeze_time("2000-01-02"):
        await realm_start_reencryption_maintenance(
            alice_backend_sock, realm, 2, pendulum_now(), {"alice": b"foo"}
        )

    # Each participant should have received a message
    rep = await message_get(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "messages": [
            {
                "count": 1,
                "body": b"foo",
                "timestamp": Pendulum(2000, 1, 2),
                "sender": alice.device_id,
            }
        ],
    }

    async def _reencrypt_with_batch_of_2(expected_size, expected_done):
        rep = await vlob_maintenance_get_reencryption_batch(alice_backend_sock, realm, 2, size=2)
        assert rep["status"] == "ok"
        assert len(rep["batch"]) == expected_size
        for entry in rep["batch"]:
            entry["blob"] = f"{entry['vlob_id']}::{entry['version']} reencrypted".encode()
        rep = await vlob_maintenance_save_reencryption_batch(
            alice_backend_sock, realm, 2, rep["batch"]
        )
        assert rep == {"status": "ok", "total": 3, "done": expected_done}

    # Should have 2 batch to reencrypt
    await _reencrypt_with_batch_of_2(expected_size=2, expected_done=2)
    await _reencrypt_with_batch_of_2(expected_size=1, expected_done=3)
    await _reencrypt_with_batch_of_2(expected_size=0, expected_done=3)

    # Finish the reencryption
    await realm_finish_reencryption_maintenance(alice_backend_sock, realm, 2)

    # Check the vlob have changed
    for vlob_id, version in vlob_atoms:
        rep = await vlob_read(alice_backend_sock, vlob_id, version, encryption_revision=2)
        assert rep["blob"] == f"{vlob_id}::{version} reencrypted".encode()


@pytest.mark.trio
async def test_reencryption_events(
    backend, alice, alice_backend_sock, alice2_backend_sock, realm, vlobs, vlob_atoms
):

    # Start listening events
    await events_subscribe(alice_backend_sock)

    with backend.event_bus.listen() as spy:
        # Start maintenance and check for events
        await realm_start_reencryption_maintenance(
            alice2_backend_sock, realm, 2, pendulum_now(), {"alice": b"foo"}
        )

        with trio.fail_after(1):
            # No guarantees those events occur before the commands' return
            await spy.wait_multiple(["realm.maintenance_started", "message.received"])

        rep = await events_listen_nowait(alice_backend_sock)
        assert rep == {
            "status": "ok",
            "event": "realm.maintenance_started",
            "realm_id": realm,
            "encryption_revision": 2,
            "garbage_collection_revision": 0,
        }
        rep = await events_listen_nowait(alice_backend_sock)
        assert rep == {"status": "ok", "event": "message.received", "index": 1}

        # Do the reencryption
        rep = await vlob_maintenance_get_reencryption_batch(alice_backend_sock, realm, 2, size=100)
        await vlob_maintenance_save_reencryption_batch(alice_backend_sock, realm, 2, rep["batch"])

        # Finish maintenance and check for events
        await realm_finish_reencryption_maintenance(alice2_backend_sock, realm, 2)

        # No guarantees those events occur before the commands' return
        await spy.wait_with_timeout("realm.maintenance_finished")

        rep = await events_listen_nowait(alice_backend_sock)
        assert rep == {
            "status": "ok",
            "event": "realm.maintenance_finished",
            "realm_id": realm,
            "encryption_revision": 2,
        }

    # Sanity check
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "no_events"}
