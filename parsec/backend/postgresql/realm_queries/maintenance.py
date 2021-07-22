# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import pendulum
from uuid import UUID
from typing import Dict

from parsec.backend.backend_events import BackendEvent
from parsec.api.protocol import DeviceID, UserID, OrganizationID, RealmRole
from parsec.backend.realm import (
    RealmStatus,
    RealmAccessError,
    RealmNotFoundError,
    RealmEncryptionRevisionError,
    RealmParticipantsMismatchError,
    RealmMaintenanceError,
    RealmInMaintenanceError,
    RealmNotInMaintenanceError,
)
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.message import send_message
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_organization_internal_id,
    q_user,
    q_device_internal_id,
    q_realm,
    q_realm_internal_id,
    STR_TO_REALM_ROLE,
    STR_TO_REALM_MAINTENANCE_TYPE,
)


_q_get_realm_status = Q(
    q_realm(
        organization_id="$organization_id",
        realm_id="$realm_id",
        select="encryption_revision, maintenance_started_by, maintenance_started_on, maintenance_type",
    )
)


async def get_realm_status(conn, organization_id: OrganizationID, realm_id: UUID) -> RealmStatus:
    rep = await conn.fetchrow(
        *_q_get_realm_status(organization_id=organization_id, realm_id=realm_id)
    )
    if not rep:
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    return RealmStatus(
        maintenance_type=STR_TO_REALM_MAINTENANCE_TYPE.get(rep["maintenance_type"]),
        maintenance_started_on=rep["maintenance_started_on"],
        maintenance_started_by=rep["maintenance_started_by"],
        encryption_revision=rep["encryption_revision"],
    )


_q_get_realm_role_for_not_revoked_with_users = Q(
    f"""
WITH cte_current_realm_roles AS (
    SELECT DISTINCT ON(user_) user_, role
    FROM  realm_user_role
    WHERE realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
    ORDER BY user_, certified_on DESC
)
SELECT user_.user_id as user_id, user_.revoked_on as revoked_on, role
FROM user_
LEFT JOIN cte_current_realm_roles
ON user_._id = cte_current_realm_roles.user_
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND user_.user_id = ANY($users_ids::VARCHAR[])
"""
)


_q_get_realm_role_for_not_revoked = Q(
    f"""
SELECT DISTINCT ON(user_)
    { q_user(_id="realm_user_role.user_", select="user_id") } as user_id,
    { q_user(_id="realm_user_role.user_", select="revoked_on") } as revoked_on,
    role
FROM  realm_user_role
WHERE realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
ORDER BY user_, certified_on DESC
"""
)


async def _get_realm_role_for_not_revoked(conn, organization_id, realm_id, users=None):
    now = pendulum.now()

    def _cook_role(row):
        if row["revoked_on"] and row["revoked_on"] <= now:
            return None
        if row["role"] is None:
            return None
        return STR_TO_REALM_ROLE[row["role"]]

    if users:
        rep = await conn.fetch(
            *_q_get_realm_role_for_not_revoked_with_users(
                organization_id=organization_id, realm_id=realm_id, users_ids=users
            )
        )
        roles = {row["user_id"]: _cook_role(row) for row in rep}
        for user in users or ():
            if user not in roles:
                raise RealmNotFoundError(f"User `{user}` doesn't exist")

        return roles

    else:
        rep = await conn.fetch(
            *_q_get_realm_role_for_not_revoked(organization_id=organization_id, realm_id=realm_id)
        )

        return {row["user_id"]: _cook_role(row) for row in rep if _cook_role(row) is not None}


_q_query_start_reencryption_maintenance_update_realm = Q(
    f"""
UPDATE realm
SET
    encryption_revision=$encryption_revision,
    maintenance_started_by={ q_device_internal_id(organization_id="$organization_id", device_id="$maintenance_started_by") },
    maintenance_started_on=$maintenance_started_on,
    maintenance_type=$maintenance_type
WHERE
    _id = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
"""
)


_q_query_start_reencryption_maintenance_update_vlob_encryption_revision = Q(
    f"""
INSERT INTO vlob_encryption_revision(
    realm,
    encryption_revision
) SELECT
    { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") },
    $encryption_revision
"""
)


@query(in_transaction=True)
async def query_start_reencryption_maintenance(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    encryption_revision: int,
    per_participant_message: Dict[UserID, bytes],
    timestamp: pendulum.DateTime,
) -> None:
    # Retrieve realm and make sure it is not under maintenance
    status = await get_realm_status(conn, organization_id, realm_id)
    if status.in_maintenance:
        raise RealmInMaintenanceError(f"Realm `{realm_id}` alrealy in maintenance")
    if encryption_revision != status.encryption_revision + 1:
        raise RealmEncryptionRevisionError("Invalid encryption revision")

    roles = await _get_realm_role_for_not_revoked(conn, organization_id, realm_id)

    if roles.get(author.user_id) != RealmRole.OWNER:
        raise RealmAccessError()

    if per_participant_message.keys() ^ roles.keys():
        raise RealmParticipantsMismatchError("Realm participants and message recipients mismatch")

    await conn.execute(
        *_q_query_start_reencryption_maintenance_update_realm(
            organization_id=organization_id,
            realm_id=realm_id,
            maintenance_started_by=author,
            maintenance_started_on=timestamp,
            maintenance_type="REENCRYPTION",
            encryption_revision=encryption_revision,
        )
    )

    await conn.execute(
        *_q_query_start_reencryption_maintenance_update_vlob_encryption_revision(
            organization_id=organization_id,
            realm_id=realm_id,
            encryption_revision=encryption_revision,
        )
    )

    await send_signal(
        conn,
        BackendEvent.REALM_MAINTENANCE_STARTED,
        organization_id=organization_id,
        author=author,
        realm_id=realm_id,
        encryption_revision=encryption_revision,
    )

    for recipient, body in per_participant_message.items():
        await send_message(conn, organization_id, author, recipient, timestamp, body)


_query_finish_reencryption_maintenance_get_info = Q(
    f"""
WITH cte_encryption_revisions AS (
    SELECT
        _id,
        encryption_revision
    FROM vlob_encryption_revision
    WHERE
        realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
        AND (encryption_revision = $encryption_revision OR encryption_revision = $encryption_revision - 1)
)
SELECT encryption_revision, COUNT(*) as count
FROM vlob_atom
INNER JOIN cte_encryption_revisions
ON cte_encryption_revisions._id = vlob_atom.vlob_encryption_revision
GROUP BY encryption_revision
ORDER BY encryption_revision
"""
)


_query_finish_reencryption_maintenance_update_realm = Q(
    f"""
UPDATE realm
SET
    maintenance_started_by=NULL,
    maintenance_started_on=NULL,
    maintenance_type=NULL
WHERE
    _id = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
"""
)


@query(in_transaction=True)
async def query_finish_reencryption_maintenance(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    encryption_revision: int,
) -> None:
    # Retrieve realm and make sure it is not under maintenance
    status = await get_realm_status(conn, organization_id, realm_id)
    roles = await _get_realm_role_for_not_revoked(conn, organization_id, realm_id, [author.user_id])
    if roles.get(author.user_id) != RealmRole.OWNER:
        raise RealmAccessError()
    if not status.in_maintenance:
        raise RealmNotInMaintenanceError(f"Realm `{realm_id}` not under maintenance")
    if encryption_revision != status.encryption_revision:
        raise RealmEncryptionRevisionError("Invalid encryption revision")

    # Test reencryption operations are over
    rep = await conn.fetch(
        *_query_finish_reencryption_maintenance_get_info(
            organization_id=organization_id,
            realm_id=realm_id,
            encryption_revision=encryption_revision,
        )
    )

    try:
        previous, current = rep
    except ValueError:
        raise RealmMaintenanceError("Reencryption operations are not over")
    assert previous["encryption_revision"] == encryption_revision - 1
    assert current["encryption_revision"] == encryption_revision
    assert previous["count"] >= current["count"]
    if previous["count"] != current["count"]:
        raise RealmMaintenanceError("Reencryption operations are not over")

    await conn.execute(
        *_query_finish_reencryption_maintenance_update_realm(
            organization_id=organization_id, realm_id=realm_id
        )
    )

    await send_signal(
        conn,
        BackendEvent.REALM_MAINTENANCE_FINISHED,
        organization_id=organization_id,
        author=author,
        realm_id=realm_id,
        encryption_revision=encryption_revision,
    )
