# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import pendulum
from uuid import UUID
from typing import Dict, Tuple, Optional

from parsec.api.protocol import DeviceID, OrganizationID
from parsec.backend.vlob import VlobVersionError, VlobNotFoundError
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_device,
    q_realm_internal_id,
    q_organization_internal_id,
    q_vlob_encryption_revision_internal_id,
)
from parsec.backend.postgresql.vlob_queries.utils import (
    _get_realm_id_from_vlob_id,
    _check_realm_and_read_access,
)


_q_read_data_without_timestamp = Q(
    f"""
SELECT
    version,
    blob,
    { q_device(_id="author", select="device_id") } as author,
    created_on
FROM vlob_atom
WHERE
    vlob_encryption_revision = {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision",
        )
    }
    AND vlob_id = $vlob_id
ORDER BY version DESC
LIMIT 1
"""
)

_q_read_data_with_timestamp = Q(
    f"""
SELECT
    version,
    blob,
    { q_device(_id="author", select="device_id") } as author,
    created_on
FROM vlob_atom
WHERE
    vlob_encryption_revision = {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision",
        )
    }
    AND vlob_id = $vlob_id
    AND created_on <= $timestamp
ORDER BY version DESC
LIMIT 1
"""
)


_q_read_data_with_version = Q(
    f"""
SELECT
    version,
    blob,
    { q_device(_id="author", select="device_id") } as author,
    created_on
FROM vlob_atom
WHERE
    vlob_encryption_revision = {
        q_vlob_encryption_revision_internal_id(
            organization_id="$organization_id",
            realm_id="$realm_id",
            encryption_revision="$encryption_revision",
        )
    }
    AND vlob_id = $vlob_id
    AND version = $version
"""
)


@query(in_transaction=True)
async def query_read(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    encryption_revision: int,
    vlob_id: UUID,
    version: Optional[int] = None,
    timestamp: Optional[pendulum.DateTime] = None,
) -> Tuple[int, bytes, DeviceID, pendulum.DateTime]:
    realm_id = await _get_realm_id_from_vlob_id(conn, organization_id, vlob_id)
    await _check_realm_and_read_access(conn, organization_id, author, realm_id, encryption_revision)

    if version is None:
        if timestamp is None:
            data = await conn.fetchrow(
                *_q_read_data_without_timestamp(
                    organization_id=organization_id,
                    realm_id=realm_id,
                    encryption_revision=encryption_revision,
                    vlob_id=vlob_id,
                )
            )
            assert data  # _get_realm_id_from_vlob_id checks vlob presence

        else:
            data = await conn.fetchrow(
                *_q_read_data_with_timestamp(
                    organization_id=organization_id,
                    realm_id=realm_id,
                    encryption_revision=encryption_revision,
                    vlob_id=vlob_id,
                    timestamp=timestamp,
                )
            )
            if not data:
                raise VlobVersionError()

    else:
        data = await conn.fetchrow(
            *_q_read_data_with_version(
                organization_id=organization_id,
                realm_id=realm_id,
                encryption_revision=encryption_revision,
                vlob_id=vlob_id,
                version=version,
            )
        )
        if not data:
            raise VlobVersionError()

    version, blob, author, created_on = data
    return version, blob, author, created_on


_q_poll_changes = Q(
    f"""
SELECT
    index,
    vlob_id,
    vlob_atom.version
FROM realm_vlob_update
LEFT JOIN vlob_atom ON realm_vlob_update.vlob_atom = vlob_atom._id
WHERE
    realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
    AND index > $checkpoint
ORDER BY index ASC
"""
)


_q_list_versions = Q(
    f"""
SELECT
    version,
    { q_device(_id="author", select="device_id") } as author,
    created_on
FROM vlob_atom
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND vlob_id = $vlob_id
ORDER BY version DESC
"""
)


@query(in_transaction=True)
async def query_poll_changes(
    conn, organization_id: OrganizationID, author: DeviceID, realm_id: UUID, checkpoint: int
) -> Tuple[int, Dict[UUID, int]]:
    await _check_realm_and_read_access(conn, organization_id, author, realm_id, None)

    ret = await conn.fetch(
        *_q_poll_changes(organization_id=organization_id, realm_id=realm_id, checkpoint=checkpoint)
    )

    changes_since_checkpoint = {src_id: src_version for _, src_id, src_version in ret}
    new_checkpoint = ret[-1][0] if ret else checkpoint
    return (new_checkpoint, changes_since_checkpoint)


@query(in_transaction=True)
async def query_list_versions(
    conn, organization_id: OrganizationID, author: DeviceID, vlob_id: UUID
) -> Dict[int, Tuple[pendulum.DateTime, DeviceID]]:
    realm_id = await _get_realm_id_from_vlob_id(conn, organization_id, vlob_id)
    await _check_realm_and_read_access(conn, organization_id, author, realm_id, None)

    rows = await conn.fetch(*_q_list_versions(organization_id=organization_id, vlob_id=vlob_id))
    assert rows

    if not rows:
        raise VlobNotFoundError(f"Vlob `{vlob_id}` doesn't exist")

    return {row["version"]: (row["created_on"], row["author"]) for row in rows}
