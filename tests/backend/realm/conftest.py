# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from uuid import UUID
from pendulum import Pendulum, now as pendulum_now

from parsec.api.protocole import (
    realm_status_serializer,
    realm_get_roles_serializer,
    realm_update_roles_serializer,
    realm_start_reencryption_maintenance_serializer,
    realm_finish_reencryption_maintenance_serializer,
    vlob_create_serializer,
    vlob_read_serializer,
    vlob_update_serializer,
    vlob_group_check_serializer,
    vlob_poll_changes_serializer,
    vlob_maintenance_get_reencryption_batch_serializer,
    vlob_maintenance_save_reencryption_batch_serializer,
)


VLOB_ID = UUID("10000000000000000000000000000000")
REALM_ID = UUID("20000000000000000000000000000000")
OTHER_VLOB_ID = UUID("30000000000000000000000000000000")


async def realm_status(sock, realm_id):
    raw_rep = await sock.send(
        realm_status_serializer.req_dumps({"cmd": "realm_status", "realm_id": realm_id})
    )
    raw_rep = await sock.recv()
    return realm_status_serializer.rep_loads(raw_rep)


async def realm_get_roles(sock, realm_id):
    raw_rep = await sock.send(
        realm_get_roles_serializer.req_dumps({"cmd": "realm_get_roles", "realm_id": realm_id})
    )
    raw_rep = await sock.recv()
    return realm_get_roles_serializer.rep_loads(raw_rep)


async def realm_update_roles(sock, realm_id, user, role):
    raw_rep = await sock.send(
        realm_update_roles_serializer.req_dumps(
            {"cmd": "realm_update_roles", "realm_id": realm_id, "user": user, "role": role}
        )
    )
    raw_rep = await sock.recv()
    return realm_update_roles_serializer.rep_loads(raw_rep)


async def realm_start_reencryption_maintenance(
    sock, realm_id, encryption_revision, per_participant_message, check_rep=True
):
    raw_rep = await sock.send(
        realm_start_reencryption_maintenance_serializer.req_dumps(
            {
                "cmd": "realm_start_reencryption_maintenance",
                "realm_id": realm_id,
                "encryption_revision": encryption_revision,
                "per_participant_message": per_participant_message,
            }
        )
    )
    raw_rep = await sock.recv()
    rep = realm_start_reencryption_maintenance_serializer.rep_loads(raw_rep)
    if check_rep:
        assert rep == {"status": "ok"}
    return rep


async def realm_finish_reencryption_maintenance(
    sock, realm_id, encryption_revision, check_rep=True
):
    raw_rep = await sock.send(
        realm_finish_reencryption_maintenance_serializer.req_dumps(
            {
                "cmd": "realm_finish_reencryption_maintenance",
                "realm_id": realm_id,
                "encryption_revision": encryption_revision,
            }
        )
    )
    raw_rep = await sock.recv()
    rep = realm_finish_reencryption_maintenance_serializer.rep_loads(raw_rep)
    if check_rep:
        assert rep == {"status": "ok"}
    return rep


async def vlob_group_check(sock, to_check):
    await sock.send(
        vlob_group_check_serializer.req_dumps({"cmd": "vlob_group_check", "to_check": to_check})
    )
    raw_rep = await sock.recv()
    return vlob_group_check_serializer.rep_loads(raw_rep)


async def vlob_create(
    sock, realm_id, vlob_id, blob, timestamp=None, encryption_revision=1, check_rep=True
):
    timestamp = timestamp or pendulum_now()
    await sock.send(
        vlob_create_serializer.req_dumps(
            {
                "cmd": "vlob_create",
                "realm_id": realm_id,
                "vlob_id": vlob_id,
                "timestamp": timestamp,
                "encryption_revision": encryption_revision,
                "blob": blob,
            }
        )
    )
    raw_rep = await sock.recv()
    rep = vlob_create_serializer.rep_loads(raw_rep)
    if check_rep:
        assert rep == {"status": "ok"}
    return rep


async def vlob_read(sock, vlob_id, version=None, timestamp=None, encryption_revision=1):
    await sock.send(
        vlob_read_serializer.req_dumps(
            {
                "cmd": "vlob_read",
                "vlob_id": vlob_id,
                "version": version,
                "timestamp": timestamp,
                "encryption_revision": encryption_revision,
            }
        )
    )
    raw_rep = await sock.recv()
    return vlob_read_serializer.rep_loads(raw_rep)


async def vlob_update(
    sock, vlob_id, version, blob, encryption_revision=1, timestamp=None, check_rep=True
):
    timestamp = timestamp or pendulum_now()
    await sock.send(
        vlob_update_serializer.req_dumps(
            {
                "cmd": "vlob_update",
                "vlob_id": vlob_id,
                "version": version,
                "timestamp": timestamp,
                "encryption_revision": encryption_revision,
                "blob": blob,
            }
        )
    )
    raw_rep = await sock.recv()
    rep = vlob_update_serializer.rep_loads(raw_rep)
    if check_rep:
        assert rep == {"status": "ok"}
    return rep


async def vlob_poll_changes(sock, realm_id, last_checkpoint):
    raw_rep = await sock.send(
        vlob_poll_changes_serializer.req_dumps(
            {"cmd": "vlob_poll_changes", "realm_id": realm_id, "last_checkpoint": last_checkpoint}
        )
    )
    raw_rep = await sock.recv()
    return vlob_poll_changes_serializer.rep_loads(raw_rep)


async def vlob_maintenance_get_reencryption_batch(sock, realm_id, encryption_revision, size=100):
    raw_rep = await sock.send(
        vlob_maintenance_get_reencryption_batch_serializer.req_dumps(
            {
                "cmd": "vlob_maintenance_get_reencryption_batch",
                "realm_id": realm_id,
                "encryption_revision": encryption_revision,
                "size": size,
            }
        )
    )
    raw_rep = await sock.recv()
    return vlob_maintenance_get_reencryption_batch_serializer.rep_loads(raw_rep)


async def vlob_maintenance_save_reencryption_batch(
    sock, realm_id, encryption_revision, batch, check_rep=True
):
    raw_rep = await sock.send(
        vlob_maintenance_save_reencryption_batch_serializer.req_dumps(
            {
                "cmd": "vlob_maintenance_save_reencryption_batch",
                "realm_id": realm_id,
                "encryption_revision": encryption_revision,
                "batch": batch,
            }
        )
    )
    raw_rep = await sock.recv()
    rep = vlob_maintenance_save_reencryption_batch_serializer.rep_loads(raw_rep)
    if check_rep:
        assert rep == {"status": "ok"}
    return rep


@pytest.fixture
async def realm(backend, alice):
    realm_id = UUID("A0000000000000000000000000000000")
    vlob_id = UUID("10000000000000000000000000000000")

    await backend.vlob.create(
        organization_id=alice.organization_id,
        author=alice.device_id,
        realm_id=realm_id,
        encryption_revision=1,
        vlob_id=vlob_id,
        timestamp=Pendulum(2000, 1, 2),
        blob=b"r:A b:1 v:1",
    )
    return realm_id


@pytest.fixture
async def vlobs(backend, alice, realm):
    vlob_ids = (UUID("10000000000000000000000000000000"), UUID("20000000000000000000000000000000"))
    await backend.vlob.update(
        organization_id=alice.organization_id,
        author=alice.device_id,
        encryption_revision=1,
        vlob_id=vlob_ids[0],
        version=2,
        timestamp=Pendulum(2000, 1, 3),
        blob=b"r:A b:1 v:2",
    )
    await backend.vlob.create(
        organization_id=alice.organization_id,
        author=alice.device_id,
        realm_id=realm,
        encryption_revision=1,
        vlob_id=vlob_ids[1],
        timestamp=Pendulum(2000, 1, 4),
        blob=b"r:A b:2 v:1",
    )
    return vlob_ids


@pytest.fixture
async def vlob_atoms(vlobs):
    return [(vlobs[0], 1), (vlobs[0], 2), (vlobs[1], 1)]


@pytest.fixture
async def other_realm(backend, bob):
    realm_id = UUID("B0000000000000000000000000000000")
    vlob_id = UUID("30000000000000000000000000000000")

    await backend.vlob.create(
        organization_id=bob.organization_id,
        author=bob.device_id,
        realm_id=realm_id,
        encryption_revision=1,
        vlob_id=vlob_id,
        timestamp=Pendulum(2000, 1, 2),
        blob=b"r:B b:10 v:1",
    )
    return realm_id
