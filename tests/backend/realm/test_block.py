# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY
from uuid import UUID, uuid4

import pendulum
import pytest
import trio
from hypothesis import given
from hypothesis import strategies as st

from parsec.api.protocol import RealmRole, block_create_serializer, block_read_serializer, packb
from parsec.backend.block import BlockTimeoutError
from parsec.backend.raid5_blockstore import (
    generate_checksum_chunk,
    rebuild_block_from_chunks,
    split_block_in_chunks,
)
from parsec.backend.realm import RealmGrantedRole
from tests.backend.common import block_create, block_read

BLOCK_ID = UUID("00000000000000000000000000000001")
VLOB_ID = UUID("00000000000000000000000000000002")
BLOCK_DATA = b"Hodi ho !"


@pytest.fixture
async def block(backend, alice, realm):
    block_id = UUID("0000000000000000000000000000000C")

    await backend.block.create(alice.organization_id, alice.device_id, block_id, realm, BLOCK_DATA)
    return block_id


@pytest.mark.trio
async def test_block_read_check_access_rights(backend, alice, bob, bob_backend_sock, realm, block):
    # User not part of the realm
    rep = await block_read(bob_backend_sock, block)
    assert rep == {"status": "not_allowed"}

    # User part of the realm with various role
    for role in (RealmRole.READER, RealmRole.CONTRIBUTOR, RealmRole.MANAGER, RealmRole.OWNER):
        await backend.realm.update_roles(
            alice.organization_id,
            RealmGrantedRole(
                certificate=b"<dummy>",
                realm_id=realm,
                user_id=bob.user_id,
                role=role,
                granted_by=alice.device_id,
            ),
        )
        rep = await block_read(bob_backend_sock, block)
        assert rep == {"status": "ok", "block": b"Hodi ho !"}


@pytest.mark.trio
async def test_block_create_check_access_rights(backend, alice, bob, bob_backend_sock, realm):
    block_id = uuid4()

    # User not part of the realm
    rep = await block_create(bob_backend_sock, block_id, realm, BLOCK_DATA, check_rep=False)
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
                certificate=b"<dummy>",
                realm_id=realm,
                user_id=bob.user_id,
                role=role,
                granted_by=alice.device_id,
            ),
        )
        block_id = uuid4()
        rep = await block_create(bob_backend_sock, block_id, realm, BLOCK_DATA, check_rep=False)
        if access_granted:
            assert rep == {"status": "ok"}

        else:
            assert rep == {"status": "not_allowed"}


@pytest.mark.trio
async def test_block_create_and_read(alice_backend_sock, realm):
    await block_create(alice_backend_sock, BLOCK_ID, realm, BLOCK_DATA)

    rep = await block_read(alice_backend_sock, BLOCK_ID)
    assert rep == {"status": "ok", "block": BLOCK_DATA}

    # Test not found as well

    dummy_id = UUID("00000000000000000000000000000002")
    rep = await block_read(alice_backend_sock, dummy_id)
    assert rep == {"status": "not_found"}


@pytest.mark.trio
@pytest.mark.raid1_blockstore
async def test_raid1_block_create_and_read(alice_backend_sock, realm):
    await test_block_create_and_read(alice_backend_sock, realm)


@pytest.mark.trio
@pytest.mark.raid1_blockstore
async def test_raid1_block_create_partial_failure(alice_backend_sock, backend, realm):
    async def mock_create(organization_id, id, block):
        await trio.sleep(0)
        raise BlockTimeoutError()

    backend.blockstore.blockstores[1].create = mock_create

    rep = await block_create(alice_backend_sock, BLOCK_ID, realm, BLOCK_DATA, check_rep=False)
    assert rep == {"status": "timeout"}


@pytest.mark.trio
@pytest.mark.raid1_blockstore
async def test_raid1_block_create_partial_exists(alice_backend_sock, alice, backend, realm):
    await backend.blockstore.blockstores[1].create(alice.organization_id, BLOCK_ID, BLOCK_DATA)

    await block_create(alice_backend_sock, BLOCK_ID, realm, BLOCK_DATA)


@pytest.mark.trio
@pytest.mark.raid1_blockstore
async def test_raid1_block_read_partial_failure(alice_backend_sock, alice, backend, block):
    async def mock_read(organization_id, id):
        await trio.sleep(0)
        raise BlockTimeoutError()

    backend.blockstore.blockstores[1].read = mock_read

    rep = await block_read(alice_backend_sock, block)
    assert rep == {"status": "ok", "block": BLOCK_DATA}


@pytest.mark.trio
@pytest.mark.raid0_blockstore
async def test_raid0_block_create_and_read(alice_backend_sock, realm):
    await test_block_create_and_read(alice_backend_sock, realm)


@pytest.mark.trio
@pytest.mark.raid5_blockstore
async def test_raid5_block_create_and_read(alice_backend_sock, realm):
    await test_block_create_and_read(alice_backend_sock, realm)


@pytest.mark.trio
@pytest.mark.raid5_blockstore
@pytest.mark.parametrize("failing_blockstore", (0, 1, 2))
async def test_raid5_block_create_single_failure(
    caplog, alice_backend_sock, backend, realm, failing_blockstore
):
    async def mock_create(organization_id, id, block):
        await trio.sleep(0)
        raise BlockTimeoutError()

    backend.blockstore.blockstores[failing_blockstore].create = mock_create

    await block_create(alice_backend_sock, BLOCK_ID, realm, BLOCK_DATA)

    # Should be notified of blockstore malfunction
    caplog.assert_occured(
        f"[warning  ] Cannot reach RAID5 blockstore #{failing_blockstore} to "
        f"create block {BLOCK_ID} [parsec.backend.raid5_blockstore]"
    )


@pytest.mark.trio
@pytest.mark.raid5_blockstore
@pytest.mark.parametrize("failing_blockstores", [(0, 1), (0, 2)])
async def test_raid5_block_create_multiple_failure(
    caplog, alice_backend_sock, backend, realm, failing_blockstores
):
    async def mock_create(organization_id, id, block):
        await trio.sleep(0)
        raise BlockTimeoutError()

    fb1, fb2 = failing_blockstores

    backend.blockstore.blockstores[fb1].create = mock_create
    backend.blockstore.blockstores[fb2].create = mock_create

    rep = await block_create(alice_backend_sock, BLOCK_ID, realm, BLOCK_DATA, check_rep=False)
    assert rep == {"status": "timeout"}

    # Should be notified of blockstore malfunction
    caplog.assert_occured(
        f"[warning  ] Cannot reach RAID5 blockstore #{fb1} to "
        f"create block {BLOCK_ID} [parsec.backend.raid5_blockstore]"
    )
    caplog.assert_occured(
        f"[warning  ] Cannot reach RAID5 blockstore #{fb2} to "
        f"create block {BLOCK_ID} [parsec.backend.raid5_blockstore]"
    )
    caplog.assert_occured(
        f"[error    ] Block {BLOCK_ID} cannot be created: Too many failing "
        "blockstores in the RAID5 cluster [parsec.backend.raid5_blockstore]"
    )


@pytest.mark.trio
@pytest.mark.raid5_blockstore
async def test_raid5_block_create_partial_exists(alice_backend_sock, alice, backend, realm):
    await backend.blockstore.blockstores[1].create(alice.organization_id, BLOCK_ID, BLOCK_DATA)

    await block_create(alice_backend_sock, BLOCK_ID, realm, BLOCK_DATA)


@pytest.mark.trio
@pytest.mark.raid5_blockstore
@pytest.mark.parametrize("failing_blockstore", (0, 1))  # Ignore checksum blockstore
async def test_raid5_block_read_single_failure(
    caplog, alice_backend_sock, alice, backend, block, failing_blockstore
):
    async def mock_read(organization_id, id):
        await trio.sleep(0)
        raise BlockTimeoutError()

    backend.blockstore.blockstores[failing_blockstore].read = mock_read

    rep = await block_read(alice_backend_sock, block)
    assert rep == {"status": "ok", "block": BLOCK_DATA}

    # Should be notified of blockstore malfunction
    caplog.assert_occured(
        f"[warning  ] Cannot reach RAID5 blockstore #{failing_blockstore} to "
        f"read block {block} [parsec.backend.raid5_blockstore]"
    )


@pytest.mark.trio
@pytest.mark.raid5_blockstore
@pytest.mark.parametrize("bad_chunk", (b"", b"too big"))
async def test_raid5_block_read_single_invalid_chunk_size(
    alice_backend_sock, alice, backend, block, bad_chunk
):
    async def mock_read(organization_id, id):
        return bad_chunk

    backend.blockstore.blockstores[1].read = mock_read

    rep = await block_read(alice_backend_sock, block)
    # A bad chunk result in a bad block, which should be detected by the client
    assert rep == {"status": "ok", "block": ANY}


@pytest.mark.trio
@pytest.mark.raid5_blockstore
@pytest.mark.parametrize("failing_blockstores", [(0, 1), (0, 2)])
async def test_raid5_block_read_multiple_failure(
    caplog, alice_backend_sock, alice, backend, block, failing_blockstores
):
    async def mock_read(organization_id, id):
        await trio.sleep(0)
        raise BlockTimeoutError()

    fb1, fb2 = failing_blockstores
    backend.blockstore.blockstores[fb1].read = mock_read
    backend.blockstore.blockstores[fb2].read = mock_read

    rep = await block_read(alice_backend_sock, block)
    assert rep == {"status": "timeout"}

    # Should be notified of blockstore malfunction
    caplog.assert_occured(
        f"[warning  ] Cannot reach RAID5 blockstore #{fb1} to "
        f"read block {block} [parsec.backend.raid5_blockstore]"
    )
    caplog.assert_occured(
        f"[warning  ] Cannot reach RAID5 blockstore #{fb2} to "
        f"read block {block} [parsec.backend.raid5_blockstore]"
    )
    caplog.assert_occured(
        f"[error    ] Block {block} cannot be read: Too many failing "
        "blockstores in the RAID5 cluster [parsec.backend.raid5_blockstore]"
    )


@pytest.mark.parametrize(
    "bad_msg",
    [
        {},
        {"id": str(BLOCK_ID), "block": BLOCK_DATA, "bad_field": "foo"},
        {"id": "not an uuid", "block": BLOCK_DATA},
        {"id": 42, "block": BLOCK_DATA},
        {"id": None, "block": BLOCK_DATA},
        {"id": str(BLOCK_ID), "block": 42},
        {"id": str(BLOCK_ID), "block": None},
        {"block": BLOCK_DATA},
    ],
)
@pytest.mark.trio
async def test_block_create_bad_msg(alice_backend_sock, bad_msg):
    await alice_backend_sock.send(packb({"cmd": "block_create", **bad_msg}))
    raw_rep = await alice_backend_sock.recv()
    rep = block_create_serializer.rep_loads(raw_rep)
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_block_read_not_found(alice_backend_sock):
    rep = await block_read(alice_backend_sock, BLOCK_ID)
    assert rep == {"status": "not_found"}


@pytest.mark.parametrize(
    "bad_msg",
    [
        {"id": str(BLOCK_ID), "bad_field": "foo"},
        {"id": "not_an_uuid"},
        {"id": 42},
        {"id": None},
        {},
    ],
)
@pytest.mark.trio
async def test_block_read_bad_msg(alice_backend_sock, bad_msg):
    await alice_backend_sock.send(packb({"cmd": "block_read", **bad_msg}))
    raw_rep = await alice_backend_sock.recv()
    rep = block_read_serializer.rep_loads(raw_rep)
    # Valid ID doesn't exists in database but this is ok given here we test
    # another layer so it's not important as long as we get our
    # `bad_message` status
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_block_conflicting_id(alice_backend_sock, realm):
    block_v1 = b"v1"
    await block_create(alice_backend_sock, BLOCK_ID, realm, block_v1)

    block_v2 = b"v2"
    rep = await block_create(alice_backend_sock, BLOCK_ID, realm, block_v2, check_rep=False)
    assert rep == {"status": "already_exists"}

    rep = await block_read(alice_backend_sock, BLOCK_ID)
    assert rep == {"status": "ok", "block": block_v1}


@pytest.mark.trio
async def test_block_check_other_organization(
    backend, sock_from_other_organization_factory, realm, block
):
    async with sock_from_other_organization_factory(backend) as sock:
        rep = await block_read(sock, block)
        assert rep == {"status": "not_found"}

        await backend.realm.create(
            sock.device.organization_id,
            RealmGrantedRole(
                certificate=b"<dummy>",
                realm_id=realm,
                user_id=sock.device.user_id,
                role=RealmRole.OWNER,
                granted_by=sock.device.device_id,
            ),
        )
        await block_create(sock, block, realm, b"other org data")

        rep = await block_read(sock, block)
        assert rep == {"status": "ok", "block": b"other org data"}


@pytest.mark.trio
async def test_access_during_maintenance(backend, alice, alice_backend_sock, realm, block):
    await backend.realm.start_reencryption_maintenance(
        alice.organization_id,
        alice.device_id,
        realm,
        2,
        {alice.user_id: b"whatever"},
        pendulum.now(),
    )

    rep = await block_create(alice_backend_sock, BLOCK_ID, realm, BLOCK_DATA, check_rep=False)
    assert rep == {"status": "in_maintenance"}

    rep = await block_read(alice_backend_sock, block)
    assert rep == {"status": "in_maintenance"}


@given(block=st.binary(max_size=2 ** 8), nb_blockstores=st.integers(min_value=3, max_value=16))
def test_split_block(block, nb_blockstores):
    nb_chunks = nb_blockstores - 1
    chunks = split_block_in_chunks(block, nb_chunks)
    assert len(chunks) == nb_chunks

    chunk_size = len(chunks[0])
    for chunk in chunks[1:]:
        assert len(chunk) == chunk_size

    rebuilt = rebuild_block_from_chunks(chunks, None)
    assert rebuilt == block

    checksum_chunk = generate_checksum_chunk(chunks)
    for missing in range(len(chunks)):
        partial_chunks = chunks.copy()
        partial_chunks[missing] = None
        rebuilt = rebuild_block_from_chunks(partial_chunks, checksum_chunk)
        assert rebuilt == block
