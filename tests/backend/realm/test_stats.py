# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from uuid import UUID, uuid4

from tests.backend.common import realm_stats
from tests.backend.common import vlob_create, block_create


REALM_ID_FAKE = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.trio
async def test_realm_stats_ok(
    backend, alice_backend_sock, realm, realm_factory, alice, sock_from_other_organization_factory
):
    rep = await realm_stats(alice_backend_sock, realm_id=realm)
    assert rep == {"status": "ok", "blocks_size": 0, "vlobs_size": 0}

    # Create new data
    await block_create(alice_backend_sock, realm_id=realm, block_id=uuid4(), block=b"1234")
    rep = await realm_stats(alice_backend_sock, realm_id=realm)
    assert rep == {"status": "ok", "blocks_size": 4, "vlobs_size": 0}

    # Create new metadata
    await vlob_create(alice_backend_sock, realm_id=realm, vlob_id=uuid4(), blob=b"1234")
    rep = await realm_stats(alice_backend_sock, realm_id=realm)
    assert rep == {"status": "ok", "blocks_size": 4, "vlobs_size": 4}

    # Test isolation between organizations
    async with sock_from_other_organization_factory(
        backend, mimick=alice.device_id
    ) as otherorg_sock:
        rep = await realm_stats(otherorg_sock, realm_id=realm)
        assert rep == {
            "status": "not_found",
            "reason": "Realm `a0000000-0000-0000-0000-000000000000` doesn't exist",
        }

        await realm_factory(backend, author=otherorg_sock.device, realm_id=realm)
        rep = await realm_stats(otherorg_sock, realm_id=realm)
        assert rep == {"status": "ok", "blocks_size": 0, "vlobs_size": 0}

        await block_create(otherorg_sock, realm_id=realm, block_id=uuid4(), block=b"12")
        await vlob_create(otherorg_sock, realm_id=realm, vlob_id=uuid4(), blob=b"12")

        rep = await realm_stats(alice_backend_sock, realm_id=realm)
        assert rep == {"status": "ok", "blocks_size": 4, "vlobs_size": 4}
        rep = await realm_stats(otherorg_sock, realm_id=realm)
        assert rep == {"status": "ok", "blocks_size": 2, "vlobs_size": 2}


@pytest.mark.trio
async def test_realm_stats_ko(bob_backend_sock, realm):
    # Test with no access to the realm
    rep = await realm_stats(bob_backend_sock, realm_id=realm)
    assert rep == {"status": "not_allowed"}

    # Test with non existant realm
    rep = await realm_stats(bob_backend_sock, realm_id=REALM_ID_FAKE)
    assert rep == {
        "status": "not_found",
        "reason": "Realm `00000000-0000-0000-0000-000000000001` doesn't exist",
    }
