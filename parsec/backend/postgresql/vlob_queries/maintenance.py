# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from uuid import UUID
from typing import List, Tuple

from parsec.backend.utils import OperationKind
from parsec.backend.realm import RealmRole
from parsec.api.protocol import DeviceID, OrganizationID
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_vlob_encryption_revision_internal_id,
    q_organization_internal_id,
)
from parsec.backend.postgresql.vlob_queries.utils import _check_realm, _check_realm_access


_q_maintenance_get_reencryption_batch = Q(
    f"""
WITH cte_to_encrypt AS (
    SELECT vlob_id, version, blob
    FROM vlob_atom
    WHERE vlob_encryption_revision = {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision - 1"
        )
    }
),
cte_encrypted AS (
    SELECT vlob_id, version
    FROM vlob_atom
    WHERE vlob_encryption_revision = {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision"
        )
    }
)
SELECT
    cte_to_encrypt.vlob_id,
    cte_to_encrypt.version,
    blob
FROM cte_to_encrypt
LEFT JOIN cte_encrypted
ON cte_to_encrypt.vlob_id = cte_encrypted.vlob_id AND cte_to_encrypt.version = cte_encrypted.version
WHERE cte_encrypted.vlob_id IS NULL
LIMIT $size
"""
)


_q_maintenance_save_reencryption_batch = Q(
    f"""
INSERT INTO vlob_atom(
    organization,
    vlob_encryption_revision,
    vlob_id,
    version,
    blob,
    size,
    author,
    created_on,
    deleted_on
)
SELECT
    organization,
    {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision",
        )
    },
    $vlob_id,
    $version,
    $blob,
    $blob_len,
    author,
    created_on,
    deleted_on
FROM vlob_atom
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND vlob_id = $vlob_id
    AND version = $version
ON CONFLICT DO NOTHING
"""
)


_q_maintenance_save_reencryption_batch_get_stat = Q(
    f"""
SELECT (
    SELECT COUNT(*)
    FROM vlob_atom
    WHERE vlob_encryption_revision = {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision - 1",
        )
    }
),
(
    SELECT COUNT(*)
    FROM vlob_atom
    WHERE vlob_encryption_revision = {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision",
        )
    }
)
"""
)


async def _check_realm_and_maintenance_access(
    conn, organization_id, author, realm_id, encryption_revision
):
    await _check_realm(
        conn, organization_id, realm_id, encryption_revision, OperationKind.MAINTENANCE
    )
    can_write_roles = (RealmRole.OWNER,)
    await _check_realm_access(conn, organization_id, realm_id, author, can_write_roles)


@query(in_transaction=True)
async def query_maintenance_get_reencryption_batch(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    encryption_revision: int,
    size: int,
) -> List[Tuple[UUID, int, bytes]]:
    await _check_realm_and_maintenance_access(
        conn, organization_id, author, realm_id, encryption_revision
    )
    rep = await conn.fetch(
        *_q_maintenance_get_reencryption_batch(
            organization_id=organization_id,
            realm_id=realm_id,
            encryption_revision=encryption_revision,
            size=size,
        )
    )
    return [(row["vlob_id"], row["version"], row["blob"]) for row in rep]


@query(in_transaction=True)
async def query_maintenance_save_reencryption_batch(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    encryption_revision: int,
    batch: List[Tuple[UUID, int, bytes]],
) -> Tuple[int, int]:
    await _check_realm_and_maintenance_access(
        conn, organization_id, author, realm_id, encryption_revision
    )
    for vlob_id, version, blob in batch:
        await conn.execute(
            *_q_maintenance_save_reencryption_batch(
                organization_id=organization_id,
                realm_id=realm_id,
                vlob_id=vlob_id,
                version=version,
                encryption_revision=encryption_revision,
                blob=blob,
                blob_len=len(blob),
            )
        )

    rep = await conn.fetchrow(
        *_q_maintenance_save_reencryption_batch_get_stat(
            organization_id=organization_id,
            realm_id=realm_id,
            encryption_revision=encryption_revision,
        )
    )

    return rep[0], rep[1]
