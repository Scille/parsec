# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Dict
from uuid import UUID

import pendulum
from pypika import Parameter

from parsec.api.protocol import DeviceID, OrganizationID, RealmRole, UserID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.message import send_message
from parsec.backend.postgresql.tables import (
    STR_TO_REALM_ROLE,
    q_device_internal_id,
    q_organization_internal_id,
    q_realm,
    q_realm_internal_id,
    q_user,
)
from parsec.backend.postgresql.utils import query
from parsec.backend.realm import (
    RealmAccessError,
    RealmEncryptionRevisionError,
    RealmInMaintenanceError,
    RealmMaintenanceError,
    RealmNotFoundError,
    RealmNotInMaintenanceError,
    RealmParticipantsMismatchError,
)


async def get_realm_status(conn, organization_id, realm_id):
    query = (
        q_realm(organization_id=Parameter("$1"), realm_id=Parameter("$2")).select(
            "encryption_revision",
            "maintenance_started_by",
            "maintenance_started_on",
            "maintenance_type",
        )
    ).get_sql()

    rep = await conn.fetchrow(query, organization_id, realm_id)
    if not rep:
        raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")
    return rep


async def get_realm_role_for_not_revoked(conn, organization_id, realm_id, users=None):
    now = pendulum.now()

    def _cook_role(row):
        if row["revoked_on"] and row["revoked_on"] <= now:
            return None
        if row["role"] is None:
            return None
        return STR_TO_REALM_ROLE[row["role"]]

    if users:

        query = """
WITH cte_current_realm_roles AS (
    SELECT DISTINCT ON(user_) user_, role
    FROM  realm_user_role
    WHERE realm = ({})
    ORDER BY user_, certified_on DESC
)
SELECT user_.user_id as user_id, user_.revoked_on as revoked_on, role
FROM user_
LEFT JOIN cte_current_realm_roles
ON user_._id = cte_current_realm_roles.user_
WHERE
    organization = ({})
    AND user_.user_id = ANY({}::VARCHAR[])
""".format(
            q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2")),
            q_organization_internal_id(Parameter("$1")),
            Parameter("$3"),
        )

        rep = await conn.fetch(query, organization_id, realm_id, users)
        roles = {row["user_id"]: _cook_role(row) for row in rep}
        for user in users or ():
            if user not in roles:
                raise RealmNotFoundError(f"User `{user}` doesn't exist")

        return roles

    else:

        query = """
SELECT DISTINCT ON(user_) ({}) as user_id, ({}) as revoked_on, role
FROM  realm_user_role
WHERE realm = ({})
ORDER BY user_, certified_on DESC
""".format(
            q_user(_id=Parameter("realm_user_role.user_")).select("user_id"),
            q_user(_id=Parameter("realm_user_role.user_")).select("revoked_on"),
            q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2")),
        )

        rep = await conn.fetch(query, organization_id, realm_id)

        return {row["user_id"]: _cook_role(row) for row in rep if _cook_role(row) is not None}


@query(in_transaction=True)
async def query_start_reencryption_maintenance(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    encryption_revision: int,
    per_participant_message: Dict[UserID, bytes],
    timestamp: pendulum.Pendulum,
) -> None:
    # Retrieve realm and make sure it is not under maintenance
    rep = await get_realm_status(conn, organization_id, realm_id)
    if rep["maintenance_type"]:
        raise RealmInMaintenanceError(f"Realm `{realm_id}` alrealy in maintenance")
    if encryption_revision != rep["encryption_revision"] + 1:
        raise RealmEncryptionRevisionError("Invalid encryption revision")

    roles = await get_realm_role_for_not_revoked(conn, organization_id, realm_id)
    if per_participant_message.keys() ^ roles.keys():
        raise RealmParticipantsMismatchError("Realm participants and message recipients mismatch")

    if roles.get(author.user_id) != RealmRole.OWNER:
        raise RealmAccessError()

    query = """
UPDATE realm
SET
    encryption_revision=$6,
    maintenance_started_by=({}),
    maintenance_started_on=$4,
    maintenance_type=$5
WHERE
    _id = ({})
""".format(
        q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$3")),
        q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2")),
    )

    await conn.execute(
        query, organization_id, realm_id, author, timestamp, "REENCRYPTION", encryption_revision
    )

    query = """
INSERT INTO vlob_encryption_revision(
    realm,
    encryption_revision
) SELECT
    ({}),
    $3
""".format(
        q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2"))
    )

    await conn.execute(query, organization_id, realm_id, encryption_revision)

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


@query(in_transaction=True)
async def query_finish_reencryption_maintenance(
    conn,
    organization_id: OrganizationID,
    author: DeviceID,
    realm_id: UUID,
    encryption_revision: int,
) -> None:
    # Retrieve realm and make sure it is not under maintenance
    rep = await get_realm_status(conn, organization_id, realm_id)
    roles = await get_realm_role_for_not_revoked(conn, organization_id, realm_id, [author.user_id])
    if roles.get(author.user_id) != RealmRole.OWNER:
        raise RealmAccessError()
    if not rep["maintenance_type"]:
        raise RealmNotInMaintenanceError(f"Realm `{realm_id}` not under maintenance")
    if encryption_revision != rep["encryption_revision"]:
        raise RealmEncryptionRevisionError("Invalid encryption revision")

    # Test reencryption operations are over

    query = """
WITH cte_encryption_revisions AS (
    SELECT
        _id,
        encryption_revision
    FROM vlob_encryption_revision
    WHERE
        realm = ({})
        AND (encryption_revision = $3 OR encryption_revision = $3 - 1)
)
SELECT encryption_revision, COUNT(*) as count
FROM vlob_atom
INNER JOIN cte_encryption_revisions
ON cte_encryption_revisions._id = vlob_atom.vlob_encryption_revision
GROUP BY encryption_revision
ORDER BY encryption_revision
    """.format(
        q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2"))
    )

    rep = await conn.fetch(query, organization_id, realm_id, encryption_revision)

    try:
        previous, current = rep
    except ValueError:
        raise RealmMaintenanceError("Reencryption operations are not over")
    assert previous["encryption_revision"] == encryption_revision - 1
    assert current["encryption_revision"] == encryption_revision
    assert previous["count"] >= current["count"]
    if previous["count"] != current["count"]:
        raise RealmMaintenanceError("Reencryption operations are not over")

    query = """
UPDATE realm
SET
    maintenance_started_by=NULL,
    maintenance_started_on=NULL,
    maintenance_type=NULL
WHERE
    _id = ({})
""".format(
        q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2"))
    )

    await conn.execute(query, organization_id, realm_id)

    await send_signal(
        conn,
        BackendEvent.REALM_MAINTENANCE_FINISHED,
        organization_id=organization_id,
        author=author,
        realm_id=realm_id,
        encryption_revision=encryption_revision,
    )
