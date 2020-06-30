# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import now as pendulum_now

from parsec.api.data import RealmRoleCertificateContent
from parsec.api.protocol import RealmRole
from tests.backend.common import realm_status, realm_update_roles


@pytest.mark.trio
async def test_status(backend, bob_backend_sock, alice_backend_sock, alice, bob, realm):
    rep = await realm_status(alice_backend_sock, realm)
    assert rep == {
        "status": "ok",
        "in_maintenance": False,
        "maintenance_type": None,
        "maintenance_started_by": None,
        "maintenance_started_on": None,
        "encryption_revision": 1,
    }
    # Cheap test on no access
    rep = await realm_status(bob_backend_sock, realm)
    assert rep == {"status": "not_allowed"}
    # Also test lesser role have access
    await realm_update_roles(
        alice_backend_sock,
        RealmRoleCertificateContent(
            author=alice.device_id,
            timestamp=pendulum_now(),
            realm_id=realm,
            user_id=bob.user_id,
            role=RealmRole.READER,
        ).dump_and_sign(alice.signing_key),
    )
    rep = await realm_status(bob_backend_sock, realm)
    assert rep == {
        "status": "ok",
        "in_maintenance": False,
        "maintenance_type": None,
        "maintenance_started_by": None,
        "maintenance_started_on": None,
        "encryption_revision": 1,
    }
