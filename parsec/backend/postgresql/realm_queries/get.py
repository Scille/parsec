# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pendulum
from uuid import UUID
from typing import Dict, List, Optional

from parsec.api.protocol import DeviceID, UserID, OrganizationID, RealmRole, MaintenanceType
from parsec.backend.realm import RealmStatus, RealmAccessError, RealmNotFoundError
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_organization_internal_id,
    q_user,
    q_user_internal_id,
    q_user_can_read_vlob,
    q_device,
    q_realm,
    q_realm_internal_id,
)
from parsec.backend.realm import RealmStats

_q_get_realm_status = Q(
    f"""
    SELECT
        { q_user_can_read_vlob(organization_id="$organization_id", realm_id="$realm_id", user_id="$user_id") } has_access,
        encryption_revision,
        { q_device(_id="maintenance_started_by", select="device_id") } maintenance_started_by,
        maintenance_started_on,
        maintenance_type
    FROM realm
    WHERE
        organization = { q_organization_internal_id("$organization_id") }
        AND realm_id = $realm_id
"""
)

_q_has_realm_access = Q(
    f"""
    SELECT
        { q_user_can_read_vlob(organization_id="$organization_id", realm_id="$realm_id", user_id="$user_id") } has_access
    FROM realm
    WHERE
        organization = { q_organization_internal_id("$organization_id") }
        AND realm_id = $realm_id
"""
)

_q_get_blocks_size_from_realm = Q(
    f"""
    SELECT SUM(size)
    FROM block
    WHERE
        realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
"""
)

_q_get_vlob_size_from_realm = Q(
    f"""
    SELECT SUM(size)
    FROM
        vlob_atom
    INNER JOIN
        realm_vlob_update ON vlob_atom._id = realm_vlob_update.vlob_atom
    WHERE
        realm_vlob_update.realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
"""
)

_q_get_current_roles = Q(
    f"""
SELECT DISTINCT ON(user_) { q_user(_id="realm_user_role.user_", select="user_id") }, role
FROM  realm_user_role
WHERE realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
ORDER BY user_, certified_on DESC
"""
)


_q_get_role_certificates = Q(
    f"""
SELECT { q_user(_id="realm_user_role.user_", select="user_id") }, role, certificate, certified_on
FROM  realm_user_role
WHERE realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
ORDER BY certified_on ASC
"""
)


_q_get_realms_for_user = Q(
    f"""
SELECT DISTINCT ON(realm) { q_realm(_id="realm_user_role.realm", select="realm_id") } as realm_id, role
FROM  realm_user_role
WHERE user_ = { q_user_internal_id(organization_id="$organization_id", user_id="$user_id") }
ORDER BY realm, certified_on DESC
"""
)


@query()
async def query_get_status(
    conn, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
) -> RealmStatus:
    ret = await conn.fetchrow(
        *_q_get_realm_status(
            organization_id=organization_id, realm_id=realm_id, user_id=author.user_id
        )
    )
    if not ret:
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    if not ret["has_access"]:
        raise RealmAccessError()

    maintenance_type = (
        MaintenanceType(ret["maintenance_type"]) if ret["maintenance_type"] is not None else None
    )
    return RealmStatus(
        maintenance_type=maintenance_type,
        maintenance_started_on=ret["maintenance_started_on"],
        maintenance_started_by=ret["maintenance_started_by"],
        encryption_revision=ret["encryption_revision"],
    )


@query(in_transaction=True)
async def query_get_stats(
    conn, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
) -> RealmStats:
    ret = await conn.fetchrow(
        *_q_has_realm_access(
            organization_id=organization_id, realm_id=realm_id, user_id=author.user_id
        )
    )
    if not ret:
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    if not ret["has_access"]:
        raise RealmAccessError()
    blocks_size_rep = await conn.fetchrow(
        *_q_get_blocks_size_from_realm(organization_id=organization_id, realm_id=realm_id)
    )
    vlobs_size_rep = await conn.fetchrow(
        *_q_get_vlob_size_from_realm(organization_id=organization_id, realm_id=realm_id)
    )

    blocks_size = blocks_size_rep["sum"] or 0
    vlobs_size = vlobs_size_rep["sum"] or 0

    return RealmStats(blocks_size=blocks_size, vlobs_size=vlobs_size)


@query()
async def query_get_current_roles(
    conn, organization_id: OrganizationID, realm_id: UUID
) -> Dict[UserID, RealmRole]:
    ret = await conn.fetch(
        *_q_get_current_roles(organization_id=organization_id, realm_id=realm_id)
    )

    if not ret:
        # Existing group must have at least one owner user
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    return {UserID(user_id): RealmRole(role) for user_id, role in ret if role is not None}


@query()
async def query_get_role_certificates(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    since: pendulum.DateTime,
) -> List[bytes]:
    ret = await conn.fetch(
        *_q_get_role_certificates(organization_id=organization_id, realm_id=realm_id)
    )

    if not ret:
        # Existing group must have at least one owner user
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    out = []
    author_current_role = None
    for user_id, role, certif, certified_on in ret:
        if not since or certified_on > since:
            out.append(certif)
        if user_id == author.user_id:
            author_current_role = role

    if author_current_role is None:
        raise RealmAccessError()

    return out


@query()
async def query_get_realms_for_user(
    conn, organization_id: OrganizationID, user: UserID
) -> Dict[UUID, Optional[RealmRole]]:
    rep = await conn.fetch(*_q_get_realms_for_user(organization_id=organization_id, user_id=user))
    return {row["realm_id"]: RealmRole(row["role"]) for row in rep if row["role"] is not None}
