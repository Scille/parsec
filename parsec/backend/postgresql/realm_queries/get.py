# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Dict, List
from uuid import UUID

import pendulum
from pypika import Parameter

from parsec.api.protocol import DeviceID, OrganizationID, RealmRole, UserID
from parsec.backend.postgresql.tables import (
    STR_TO_REALM_MAINTENANCE_TYPE,
    STR_TO_REALM_ROLE,
    q_device,
    q_realm,
    q_realm_internal_id,
    q_user,
    q_user_can_read_vlob,
    q_user_internal_id,
)
from parsec.backend.postgresql.utils import query
from parsec.backend.realm import RealmAccessError, RealmNotFoundError, RealmStatus

_q_get_realm_status = (
    q_realm(organization_id=Parameter("$1"), realm_id=Parameter("$2")).select(
        q_user_can_read_vlob(
            organization_id=Parameter("$1"), realm_id=Parameter("$2"), user_id=Parameter("$3")
        ).as_("has_access"),
        "encryption_revision",
        q_device(_id=Parameter("maintenance_started_by"))
        .select("device_id")
        .as_("maintenance_started_by"),
        "maintenance_started_on",
        "maintenance_type",
    )
).get_sql()


_q_get_current_roles = """
SELECT DISTINCT ON(user_) ({}), role
FROM  realm_user_role
WHERE realm = ({})
ORDER BY user_, certified_on DESC
""".format(
    q_user(_id=Parameter("user_")).select("user_id"),
    q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2")),
)


_q_get_role_certificates = """
SELECT ({}), role, certificate, certified_on
FROM  realm_user_role
WHERE realm = ({})
ORDER BY certified_on ASC
""".format(
    q_user(_id=Parameter("user_")).select("user_id"),
    q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2")),
)


_q_get_realms_for_user = """
SELECT DISTINCT ON(realm) ({}) as realm_id, role
FROM  realm_user_role
WHERE user_ = ({})
ORDER BY realm, certified_on DESC
""".format(
    q_realm(_id=Parameter("realm")).select("realm_id"),
    q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$2")),
)


@query()
async def query_get_status(
    conn, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
) -> RealmStatus:
    ret = await conn.fetchrow(_q_get_realm_status, organization_id, realm_id, author.user_id)
    if not ret:
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    if not ret["has_access"]:
        raise RealmAccessError()

    return RealmStatus(
        maintenance_type=STR_TO_REALM_MAINTENANCE_TYPE.get(ret["maintenance_type"]),
        maintenance_started_on=ret["maintenance_started_on"],
        maintenance_started_by=ret["maintenance_started_by"],
        encryption_revision=ret["encryption_revision"],
    )


@query()
async def query_get_current_roles(
    conn, organization_id: OrganizationID, realm_id: UUID
) -> Dict[UserID, RealmRole]:
    ret = await conn.fetch(_q_get_current_roles, organization_id, realm_id)

    if not ret:
        # Existing group must have at least one owner user
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    return {UserID(user_id): STR_TO_REALM_ROLE[role] for user_id, role in ret if role is not None}


@query()
async def query_get_role_certificates(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    since: pendulum.Pendulum,
) -> List[bytes]:
    ret = await conn.fetch(_q_get_role_certificates, organization_id, realm_id)

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
) -> Dict[UUID, RealmRole]:
    rep = await conn.fetch(_q_get_realms_for_user, organization_id, user)
    return {
        row["realm_id"]: STR_TO_REALM_ROLE.get(row["role"])
        for row in rep
        if row["role"] is not None
    }
