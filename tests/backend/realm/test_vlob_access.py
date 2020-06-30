# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID, uuid4

import pytest
from pendulum import Pendulum

from parsec.api.protocol import (
    RealmRole,
    packb,
    vlob_create_serializer,
    vlob_list_versions_serializer,
    vlob_read_serializer,
    vlob_update_serializer,
)
from parsec.backend.realm import RealmGrantedRole
from tests.backend.common import vlob_create, vlob_list_versions, vlob_read, vlob_update
from tests.common import freeze_time

VLOB_ID = UUID("00000000000000000000000000000001")


@pytest.mark.trio
async def test_create_and_read(alice, alice_backend_sock, alice2_backend_sock, realm):
    blob = b"Initial commit."
    with freeze_time("2000-01-02"):
        await vlob_create(alice_backend_sock, realm, VLOB_ID, blob)

    rep = await vlob_read(alice2_backend_sock, VLOB_ID)
    assert rep == {
        "status": "ok",
        "version": 1,
        "blob": blob,
        "author": alice.device_id,
        "timestamp": Pendulum(2000, 1, 2),
    }


@pytest.mark.trio
async def test_create_bad_timestamp(alice, alice_backend_sock, realm):
    blob = b"Initial commit."
    d1 = Pendulum(2000, 1, 1)
    with freeze_time(d1):
        d2 = d1.add(seconds=3600)
        rep = await vlob_create(
            alice_backend_sock, realm, VLOB_ID, blob, timestamp=d2, check_rep=False
        )
    assert rep == {"status": "bad_timestamp", "reason": "Timestamp is out of date."}


@pytest.mark.parametrize(
    "bad_msg",
    [
        {"blob": b"...", "bad_field": "foo"},
        {"blob": 42},
        {"blob": None},
        {"id": "<not an uuid>", "blob": b"..."},
        {"id": 42, "blob": b"..."},
    ],
)
@pytest.mark.trio
async def test_create_bad_msg(alice_backend_sock, bad_msg):
    await alice_backend_sock.send(packb({"cmd": "vlob_create", **bad_msg}))
    raw_rep = await alice_backend_sock.recv()
    rep = vlob_create_serializer.rep_loads(raw_rep)
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_create_but_already_exists(alice_backend_sock, realm):
    blob = b"Initial commit."

    await vlob_create(alice_backend_sock, realm, VLOB_ID, blob)

    rep = await vlob_create(alice_backend_sock, realm, VLOB_ID, blob, check_rep=False)
    assert rep["status"] == "already_exists"


@pytest.mark.trio
async def test_create_check_access_rights(backend, alice, bob, bob_backend_sock, realm, vlobs):
    vlob_id = uuid4()

    # User not part of the realm
    rep = await vlob_create(bob_backend_sock, realm, vlob_id, b"Initial version.", check_rep=False)
    assert rep == {"status": "not_allowed"}

    # User part of the realm with various role
    for role, access_granted in [
        (RealmRole.READER, False),
        (RealmRole.CONTRIBUTOR, True),
        (RealmRole.MANAGER, True),
        (RealmRole.OWNER, True),
    ]:
        await backend.realm.update_roles(
            alice.organization_id,
            RealmGrantedRole(
                certificate=b"dummy",
                realm_id=realm,
                user_id=bob.user_id,
                role=role,
                granted_by=alice.device_id,
            ),
        )
        vlob_id = uuid4()
        rep = await vlob_create(
            bob_backend_sock, realm, vlob_id, b"Initial version.", check_rep=False
        )
        if access_granted:
            assert rep == {"status": "ok"}

        else:
            assert rep == {"status": "not_allowed"}


@pytest.mark.trio
async def test_read_not_found(alice_backend_sock):
    rep = await vlob_read(alice_backend_sock, VLOB_ID)
    assert rep == {
        "status": "not_found",
        "reason": "Vlob `00000000-0000-0000-0000-000000000001` doesn't exist",
    }


@pytest.mark.trio
async def test_read_ok(alice, alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0])
    assert rep == {
        "status": "ok",
        "blob": b"r:A b:1 v:2",
        "version": 2,
        "author": alice.device_id,
        "timestamp": Pendulum(2000, 1, 3),
    }


@pytest.mark.trio
async def test_read_ok_v1(alice, alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0], version=1)
    assert rep == {
        "status": "ok",
        "blob": b"r:A b:1 v:1",
        "version": 1,
        "author": alice.device_id,
        "timestamp": Pendulum(2000, 1, 2),
    }


@pytest.mark.trio
async def test_read_ok_timestamp_after_v2(alice, alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0], timestamp=Pendulum(2000, 1, 4))
    assert rep == {
        "status": "ok",
        "blob": b"r:A b:1 v:2",
        "version": 2,
        "author": alice.device_id,
        "timestamp": Pendulum(2000, 1, 3),
    }


@pytest.mark.trio
async def test_read_ok_timestamp_is_v2(alice, alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0], timestamp=Pendulum(2000, 1, 3))
    assert rep == {
        "status": "ok",
        "blob": b"r:A b:1 v:2",
        "version": 2,
        "author": alice.device_id,
        "timestamp": Pendulum(2000, 1, 3),
    }


@pytest.mark.trio
async def test_read_ok_timestamp_between_v1_and_v2(alice, alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0], timestamp=Pendulum(2000, 1, 2, 10))
    assert rep == {
        "status": "ok",
        "blob": b"r:A b:1 v:1",
        "version": 1,
        "author": alice.device_id,
        "timestamp": Pendulum(2000, 1, 2),
    }


@pytest.mark.trio
async def test_read_ok_timestamp_is_v1(alice, alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0], timestamp=Pendulum(2000, 1, 2))
    assert rep == {
        "status": "ok",
        "blob": b"r:A b:1 v:1",
        "version": 1,
        "author": alice.device_id,
        "timestamp": Pendulum(2000, 1, 2),
    }


@pytest.mark.trio
async def test_read_before_v1(alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0], timestamp=Pendulum(2000, 1, 1))
    assert rep == {"status": "bad_version"}


@pytest.mark.trio
async def test_read_check_access_rights(backend, alice, bob, bob_backend_sock, realm, vlobs):
    # Not part of the realm
    rep = await vlob_read(bob_backend_sock, vlobs[0])
    assert rep == {"status": "not_allowed"}

    for role in RealmRole:
        await backend.realm.update_roles(
            alice.organization_id,
            RealmGrantedRole(
                certificate=b"dummy",
                realm_id=realm,
                user_id=bob.user_id,
                role=role,
                granted_by=alice.device_id,
            ),
        )
        rep = await vlob_read(bob_backend_sock, vlobs[0])
        assert rep["status"] == "ok"


@pytest.mark.trio
async def test_read_other_organization(backend, sock_from_other_organization_factory, vlobs):
    async with sock_from_other_organization_factory(backend) as sock:
        rep = await vlob_read(sock, vlobs[0])
    assert rep == {
        "status": "not_found",
        "reason": "Vlob `10000000-0000-0000-0000-000000000000` doesn't exist",
    }


@pytest.mark.parametrize(
    "bad_msg",
    [
        {"id": str(VLOB_ID), "bad_field": "foo"},
        {"id": "<not an uuid>"},
        {"id": str(VLOB_ID)},  # TODO: really bad ?
        {"id": 42},
        {"id": None},
        {"id": str(VLOB_ID), "version": 0},
        {"id": str(VLOB_ID), "version": "foo"},
        {},
    ],
)
@pytest.mark.trio
async def test_read_bad_msg(alice_backend_sock, bad_msg):
    await alice_backend_sock.send(packb({"cmd": "vlob_read", **bad_msg}))
    raw_rep = await alice_backend_sock.recv()
    rep = vlob_read_serializer.rep_loads(raw_rep)
    # Id and trust_seed are invalid anyway, but here we test another layer
    # so it's not important as long as we get our `bad_message` status
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_read_bad_version(alice_backend_sock, vlobs):
    rep = await vlob_read(alice_backend_sock, vlobs[0], version=3)
    assert rep == {"status": "bad_version"}


@pytest.mark.trio
async def test_update_ok(alice_backend_sock, vlobs):
    await vlob_update(alice_backend_sock, vlobs[0], version=3, blob=b"Next version.")


@pytest.mark.trio
async def test_update_bad_timestamp(alice, alice_backend_sock, vlobs):
    blob = b"Initial commit."
    d1 = Pendulum(2000, 1, 1)
    with freeze_time(d1):
        d2 = d1.add(seconds=3600)
        rep = await vlob_update(
            alice_backend_sock, vlobs[0], version=3, blob=blob, timestamp=d2, check_rep=False
        )
    assert rep == {"status": "bad_timestamp", "reason": "Timestamp is out of date."}


@pytest.mark.trio
async def test_update_not_found(alice_backend_sock):
    rep = await vlob_update(
        alice_backend_sock, VLOB_ID, version=2, blob=b"Next version.", check_rep=False
    )
    assert rep == {
        "status": "not_found",
        "reason": "Vlob `00000000-0000-0000-0000-000000000001` doesn't exist",
    }


@pytest.mark.trio
async def test_update_check_access_rights(backend, alice, bob, bob_backend_sock, realm, vlobs):
    # User not part of the realm
    rep = await vlob_update(
        bob_backend_sock, vlobs[0], version=3, blob=b"Next version.", check_rep=False
    )
    assert rep == {"status": "not_allowed"}

    # User part of the realm with various role
    next_version = 3
    for role, access_granted in [
        (RealmRole.READER, False),
        (RealmRole.CONTRIBUTOR, True),
        (RealmRole.MANAGER, True),
        (RealmRole.OWNER, True),
    ]:
        await backend.realm.update_roles(
            alice.organization_id,
            RealmGrantedRole(
                certificate=b"dummy",
                realm_id=realm,
                user_id=bob.user_id,
                role=role,
                granted_by=alice.device_id,
            ),
        )
        rep = await vlob_update(
            bob_backend_sock, vlobs[0], version=next_version, blob=b"Next version.", check_rep=False
        )
        if access_granted:
            assert rep == {"status": "ok"}
            next_version += 1

        else:
            assert rep == {"status": "not_allowed"}


@pytest.mark.trio
async def test_update_other_organization(backend, sock_from_other_organization_factory, vlobs):
    async with sock_from_other_organization_factory(backend) as sock:
        rep = await vlob_update(sock, vlobs[0], version=3, blob=b"Next version.", check_rep=False)
    assert rep == {
        "status": "not_found",
        "reason": "Vlob `10000000-0000-0000-0000-000000000000` doesn't exist",
    }


@pytest.mark.parametrize(
    "bad_msg",
    [
        {"id": str(VLOB_ID), "version": 42, "blob": b"...", "bad_field": "foo"},
        {"id": str(VLOB_ID), "version": 42, "blob": None},
        {"id": str(VLOB_ID), "version": 42, "blob": 42},
        {"id": str(VLOB_ID), "version": 42},
        {"id": str(VLOB_ID), "version": None, "blob": b"..."},
        {"id": str(VLOB_ID), "version": -1, "blob": b"..."},
        {"id": 42, "version": 42, "blob": b"..."},
        {"id": None, "version": 42, "blob": b"..."},
        {"version": 42, "blob": b"..."},
        {},
    ],
)
@pytest.mark.trio
async def test_update_bad_msg(alice_backend_sock, bad_msg):
    await alice_backend_sock.send(packb({"cmd": "vlob_update", **bad_msg}))
    raw_rep = await alice_backend_sock.recv()
    rep = vlob_update_serializer.rep_loads(raw_rep)
    # Id and version are invalid anyway, but here we test another layer
    # so it's not important as long as we get our `bad_message` status
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_update_bad_version(alice_backend_sock, vlobs):
    rep = await vlob_update(
        alice_backend_sock, vlobs[0], version=4, blob=b"Next version.", check_rep=False
    )
    assert rep == {"status": "bad_version"}


@pytest.mark.trio
async def test_bad_encryption_revision(backend, alice, alice_backend_sock, realm, vlobs):
    rep = await vlob_create(
        alice_backend_sock,
        realm,
        VLOB_ID,
        blob=b"First version.",
        encryption_revision=42,
        check_rep=False,
    )
    assert rep == {"status": "bad_encryption_revision"}

    rep = await vlob_read(alice_backend_sock, vlobs[0], encryption_revision=42)
    assert rep == {"status": "bad_encryption_revision"}

    rep = await vlob_update(
        alice_backend_sock,
        vlobs[0],
        version=3,
        blob=b"Next version.",
        encryption_revision=42,
        check_rep=False,
    )
    assert rep == {"status": "bad_encryption_revision"}


@pytest.mark.trio
async def test_list_versions_ok(alice, alice_backend_sock, vlobs):
    rep = await vlob_list_versions(alice_backend_sock, vlobs[0])
    assert rep == {
        "status": "ok",
        "versions": {
            1: (Pendulum(2000, 1, 2, 00, 00, 00), alice.device_id),
            2: (Pendulum(2000, 1, 3, 00, 00, 00), alice.device_id),
        },
    }


@pytest.mark.trio
async def test_list_versions_not_found(alice_backend_sock):
    rep = await vlob_list_versions(alice_backend_sock, VLOB_ID)
    assert rep == {
        "status": "not_found",
        "reason": "Vlob `00000000-0000-0000-0000-000000000001` doesn't exist",
    }


@pytest.mark.trio
async def test_list_versions_check_access_rights(
    backend, alice, bob, bob_backend_sock, realm, vlobs
):
    # Not part of the realm
    rep = await vlob_list_versions(bob_backend_sock, vlobs[0])
    assert rep == {"status": "not_allowed"}

    for role in RealmRole:
        await backend.realm.update_roles(
            alice.organization_id,
            RealmGrantedRole(
                certificate=b"dummy",
                realm_id=realm,
                user_id=bob.user_id,
                role=role,
                granted_by=alice.device_id,
            ),
        )
        rep = await vlob_list_versions(bob_backend_sock, vlobs[0])
        assert rep["status"] == "ok"


@pytest.mark.trio
async def test_list_versions_other_organization(
    backend, sock_from_other_organization_factory, vlobs
):
    async with sock_from_other_organization_factory(backend) as sock:
        rep = await vlob_list_versions(sock, vlobs[0])
    assert rep == {
        "status": "not_found",
        "reason": "Vlob `10000000-0000-0000-0000-000000000000` doesn't exist",
    }


@pytest.mark.parametrize(
    "bad_msg",
    [
        {"id": str(VLOB_ID), "bad_field": "foo"},
        {"id": "<not an uuid>"},
        {"id": str(VLOB_ID)},  # TODO: really bad ?
        {"id": 42},
        {"id": None},
        {"id": str(VLOB_ID), "version": 1},
        {},
    ],
)
@pytest.mark.trio
async def test_list_versions_bad_msg(alice_backend_sock, bad_msg):
    await alice_backend_sock.send(packb({"cmd": "vlob_list_versions", **bad_msg}))
    raw_rep = await alice_backend_sock.recv()
    rep = vlob_list_versions_serializer.rep_loads(raw_rep)
    # Id and trust_seed are invalid anyway, but here we test another layer
    # so it's not important as long as we get our `bad_message` status
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_access_during_maintenance(backend, alice, alice_backend_sock, realm, vlobs):
    await backend.realm.start_reencryption_maintenance(
        alice.organization_id,
        alice.device_id,
        realm,
        2,
        {alice.user_id: b"whatever"},
        Pendulum(2000, 1, 2),
    )

    rep = await vlob_create(
        alice_backend_sock,
        realm,
        VLOB_ID,
        blob=b"First version.",
        encryption_revision=2,
        check_rep=False,
    )
    assert rep == {"status": "in_maintenance"}

    rep = await vlob_read(alice_backend_sock, vlobs[0], encryption_revision=2)
    assert rep == {"status": "in_maintenance"}

    rep = await vlob_update(
        alice_backend_sock,
        vlobs[0],
        version=3,
        blob=b"Next version.",
        encryption_revision=2,
        check_rep=False,
    )
    assert rep == {"status": "in_maintenance"}
