# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY
from uuid import uuid4

import pytest

from parsec.api.protocol import apiv1_organization_stats_serializer
from tests.backend.common import block_create, vlob_create


async def organization_stats(sock, organization_id):
    raw_rep = await sock.send(
        apiv1_organization_stats_serializer.req_dumps(
            {"cmd": "organization_stats", "organization_id": organization_id}
        )
    )
    raw_rep = await sock.recv()
    return apiv1_organization_stats_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_organization_stats(coolorg, alice_backend_sock, administration_backend_sock, realm):
    rep = await organization_stats(administration_backend_sock, coolorg.organization_id)
    assert rep == {"status": "ok", "data_size": 0, "metadata_size": ANY, "users": 3}
    initial_metadata_size = rep["metadata_size"]

    # Create new metadata
    await vlob_create(alice_backend_sock, realm_id=realm, vlob_id=uuid4(), blob=b"1234")
    rep = await organization_stats(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "data_size": 0,
        "metadata_size": initial_metadata_size + 4,
        "users": 3,
    }

    # Create new data
    await block_create(alice_backend_sock, realm_id=realm, block_id=uuid4(), block=b"1234")
    rep = await organization_stats(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "data_size": 4,
        "metadata_size": initial_metadata_size + 4,
        "users": 3,
    }


@pytest.mark.trio
async def test_stats_unknown_organization(administration_backend_sock):
    rep = await organization_stats(administration_backend_sock, organization_id="dummy")
    assert rep == {"status": "not_found"}
