# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Dict, List, Optional, Tuple
from uuid import UUID

import pendulum
from pypika import Parameter
from triopg import UniqueViolationError

from parsec.api.protocol import DeviceID, OrganizationID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.postgresql.handler import PGHandler, retry_on_unique_violation, send_signal
from parsec.backend.postgresql.realm_queries.maintenance import RealmNotFoundError, get_realm_status
from parsec.backend.postgresql.tables import (
    STR_TO_REALM_ROLE,
    q_device,
    q_device_internal_id,
    q_organization_internal_id,
    q_realm_in_maintenance,
    q_realm_internal_id,
    q_user_can_read_vlob,
    q_user_internal_id,
    q_vlob_atom,
    q_vlob_encryption_revision_internal_id,
    t_vlob_encryption_revision,
)
from parsec.backend.postgresql.utils import Query
from parsec.backend.realm import RealmRole
from parsec.backend.vlob import (
    BaseVlobComponent,
    VlobAccessError,
    VlobAlreadyExistsError,
    VlobEncryptionRevisionError,
    VlobInMaintenanceError,
    VlobNotFoundError,
    VlobNotInMaintenanceError,
    VlobTimestampError,
    VlobVersionError,
)


async def _check_realm(
    conn, organization_id, realm_id, encryption_revision, expected_maintenance=False
):
    try:
        rep = await get_realm_status(conn, organization_id, realm_id)

    except RealmNotFoundError as exc:
        raise VlobNotFoundError(*exc.args) from exc

    if expected_maintenance is False:
        if rep["maintenance_type"]:
            raise VlobInMaintenanceError("Data realm is currently under maintenance")
    elif expected_maintenance is True:
        if not rep["maintenance_type"]:
            raise VlobNotInMaintenanceError(f"Realm `{realm_id}` not under maintenance")

    if encryption_revision is not None and rep["encryption_revision"] != encryption_revision:
        raise VlobEncryptionRevisionError()


async def _check_realm_access(conn, organization_id, realm_id, author, allowed_roles):
    query = """
WITH cte_current_realm_roles AS (
    SELECT DISTINCT ON(user_) user_, role
    FROM  realm_user_role
    WHERE realm = ({})
    ORDER BY user_, certified_on DESC
)
SELECT role
FROM user_
LEFT JOIN cte_current_realm_roles
ON user_._id = cte_current_realm_roles.user_
WHERE user_._id = ({})
        """.format(
        q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2")),
        q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$3")),
    )
    rep = await conn.fetchrow(query, organization_id, realm_id, author.user_id)

    if not rep:
        raise VlobNotFoundError(f"User `{author.user_id}` doesn't exist")

    if STR_TO_REALM_ROLE.get(rep[0]) not in allowed_roles:
        raise VlobAccessError()


async def _check_realm_and_maintenance_access(
    conn, organization_id, author, realm_id, encryption_revision
):
    await _check_realm(
        conn, organization_id, realm_id, encryption_revision, expected_maintenance=True
    )
    can_write_roles = (RealmRole.OWNER,)
    await _check_realm_access(conn, organization_id, realm_id, author, can_write_roles)


async def _check_realm_and_write_access(
    conn, organization_id, author, realm_id, encryption_revision
):
    await _check_realm(conn, organization_id, realm_id, encryption_revision)
    can_write_roles = (RealmRole.OWNER, RealmRole.MANAGER, RealmRole.CONTRIBUTOR)
    await _check_realm_access(conn, organization_id, realm_id, author, can_write_roles)


async def _check_realm_and_read_access(
    conn, organization_id, author, realm_id, encryption_revision
):
    await _check_realm(conn, organization_id, realm_id, encryption_revision)
    can_read_roles = (RealmRole.OWNER, RealmRole.MANAGER, RealmRole.CONTRIBUTOR, RealmRole.READER)
    await _check_realm_access(conn, organization_id, realm_id, author, can_read_roles)


async def _vlob_updated(
    conn, vlob_atom_internal_id, organization_id, author, realm_id, src_id, src_version=1
):
    query = """
INSERT INTO realm_vlob_update (
realm, index, vlob_atom
)
SELECT
({q_realm}),
(
    SELECT COALESCE(MAX(index) + 1, 1)
    FROM realm_vlob_update
    WHERE realm = ({q_realm})
),
$3
RETURNING index
""".format(
        q_realm=q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2"))
    )

    index = await conn.fetchval(query, organization_id, realm_id, vlob_atom_internal_id)

    await send_signal(
        conn,
        BackendEvent.REALM_VLOBS_UPDATED,
        organization_id=organization_id,
        author=author,
        realm_id=realm_id,
        checkpoint=index,
        src_id=src_id,
        src_version=src_version,
    )


async def _get_realm_id_from_vlob_id(conn, organization_id, vlob_id):
    query = """
SELECT
    realm.realm_id
FROM vlob_atom
INNER JOIN vlob_encryption_revision
ON  vlob_atom.vlob_encryption_revision = vlob_encryption_revision._id
INNER JOIN realm
ON vlob_encryption_revision.realm = realm._id
WHERE vlob_atom._id = ({})
LIMIT 1
    """.format(
        q_vlob_atom(organization_id=Parameter("$1"), vlob_id=Parameter("$2")).select("_id").limit(1)
    )

    realm_id = await conn.fetchval(query, organization_id, vlob_id)
    if not realm_id:
        raise VlobNotFoundError(f"Vlob `{vlob_id}` doesn't exist")
    return realm_id


class PGVlobComponent(BaseVlobComponent):
    def __init__(self, dbh: PGHandler):
        self.dbh = dbh

    @retry_on_unique_violation
    async def create(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        vlob_id: UUID,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            await _check_realm_and_write_access(
                conn, organization_id, author, realm_id, encryption_revision
            )

            # Actually create the vlob
            try:
                query = """
INSERT INTO vlob_atom (
    organization,
    vlob_encryption_revision,
    vlob_id,
    version,
    blob,
    size,
    author,
    created_on
)
SELECT
    ({}),
    ({}),
    $5,
    1,
    $6,
    $7,
    ({}),
    $8
RETURNING _id
""".format(
                    q_organization_internal_id(organization_id=Parameter("$1")),
                    Query.from_(t_vlob_encryption_revision)
                    .where(
                        (
                            t_vlob_encryption_revision.realm
                            == q_realm_internal_id(
                                organization_id=Parameter("$1"), realm_id=Parameter("$3")
                            )
                        )
                        & (t_vlob_encryption_revision.encryption_revision == Parameter("$4"))
                    )
                    .select("_id"),
                    q_device_internal_id(
                        organization_id=Parameter("$1"), device_id=Parameter("$2")
                    ),
                )

                vlob_atom_internal_id = await conn.fetchval(
                    query,
                    organization_id,
                    author,
                    realm_id,
                    encryption_revision,
                    vlob_id,
                    blob,
                    len(blob),
                    timestamp,
                )

            except UniqueViolationError:
                raise VlobAlreadyExistsError()

            await _vlob_updated(
                conn, vlob_atom_internal_id, organization_id, author, realm_id, vlob_id
            )

    async def read(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: Optional[int] = None,
        timestamp: Optional[pendulum.Pendulum] = None,
    ) -> Tuple[int, bytes, DeviceID, pendulum.Pendulum]:

        async with self.dbh.pool.acquire() as conn, conn.transaction():

            realm_id = await _get_realm_id_from_vlob_id(conn, organization_id, vlob_id)
            await _check_realm_and_read_access(
                conn, organization_id, author, realm_id, encryption_revision
            )

            if version is None:
                if timestamp is None:
                    query = """
SELECT
    version,
    blob,
    ({}) as author,
    created_on
FROM vlob_atom
WHERE
    vlob_encryption_revision = ({})
    AND vlob_id = $4
ORDER BY version DESC
LIMIT 1
""".format(
                        q_device(_id=Parameter("author")).select("device_id"),
                        q_vlob_encryption_revision_internal_id(
                            organization_id=Parameter("$1"),
                            realm_id=Parameter("$2"),
                            encryption_revision=Parameter("$3"),
                        ),
                    )

                    data = await conn.fetchrow(
                        query, organization_id, realm_id, encryption_revision, vlob_id
                    )
                    assert data  # _get_realm_id_from_vlob_id checks vlob presence

                else:
                    query = """
SELECT
    version,
    blob,
    ({}) as author,
    created_on
FROM vlob_atom
WHERE
    vlob_encryption_revision = ({})
    AND vlob_id = $4
    AND created_on <= $5
ORDER BY version DESC
LIMIT 1
""".format(
                        q_device(_id=Parameter("author")).select("device_id"),
                        q_vlob_encryption_revision_internal_id(
                            organization_id=Parameter("$1"),
                            realm_id=Parameter("$2"),
                            encryption_revision=Parameter("$3"),
                        ),
                    )

                    data = await conn.fetchrow(
                        query, organization_id, realm_id, encryption_revision, vlob_id, timestamp
                    )
                    if not data:
                        raise VlobVersionError()

            else:
                query = """
SELECT
    version,
    blob,
    ({}) as author,
    created_on
FROM vlob_atom
WHERE
    vlob_encryption_revision = ({})
    AND vlob_id = $4
    AND version = $5
""".format(
                    q_device(_id=Parameter("author")).select("device_id"),
                    q_vlob_encryption_revision_internal_id(
                        organization_id=Parameter("$1"),
                        realm_id=Parameter("$2"),
                        encryption_revision=Parameter("$3"),
                    ),
                )

                data = await conn.fetchrow(
                    query, organization_id, realm_id, encryption_revision, vlob_id, version
                )
                if not data:
                    raise VlobVersionError()

        return list(data)

    @retry_on_unique_violation
    async def update(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: int,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        async with self.dbh.pool.acquire() as conn, conn.transaction():

            realm_id = await _get_realm_id_from_vlob_id(conn, organization_id, vlob_id)
            await _check_realm_and_write_access(
                conn, organization_id, author, realm_id, encryption_revision
            )

            query = """
SELECT
    version,
    created_on
FROM vlob_atom
WHERE
    organization = ({})
    AND vlob_id = $2
ORDER BY version DESC LIMIT 1
""".format(
                q_organization_internal_id(Parameter("$1"))
            )

            previous = await conn.fetchrow(query, organization_id, vlob_id)
            if not previous:
                raise VlobNotFoundError(f"Vlob `{vlob_id}` doesn't exist")

            elif previous["version"] != version - 1:
                raise VlobVersionError()

            elif previous["created_on"] > timestamp:
                raise VlobTimestampError()

            query = """
INSERT INTO vlob_atom (
    organization,
    vlob_encryption_revision,
    vlob_id,
    version,
    blob,
    size,
    author,
    created_on
)
SELECT
    ({}),
    ({}),
    $5,
    $9,
    $6,
    $7,
    ({}),
    $8
RETURNING _id
""".format(
                q_organization_internal_id(Parameter("$1")),
                q_vlob_encryption_revision_internal_id(
                    organization_id=Parameter("$1"),
                    realm_id=Parameter("$3"),
                    encryption_revision=Parameter("$4"),
                ),
                q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$2")),
            )

            try:
                vlob_atom_internal_id = await conn.fetchval(
                    query,
                    organization_id,
                    author,
                    realm_id,
                    encryption_revision,
                    vlob_id,
                    blob,
                    len(blob),
                    timestamp,
                    version,
                )

            except UniqueViolationError:
                # Should not occurs in theory given we are in a transaction
                raise VlobVersionError()

            await _vlob_updated(
                conn, vlob_atom_internal_id, organization_id, author, realm_id, vlob_id, version
            )

    async def group_check(
        self, organization_id: OrganizationID, author: DeviceID, to_check: List[dict]
    ) -> List[dict]:
        changed = []
        to_check_dict = {}
        for x in to_check:
            if x["version"] == 0:
                changed.append({"vlob_id": x["vlob_id"], "version": 0})
            else:
                to_check_dict[x["vlob_id"]] = x

        async with self.dbh.pool.acquire() as conn:
            query = """
SELECT DISTINCT ON (vlob_id) vlob_id, version
FROM vlob_atom
WHERE
    organization = ({})
    AND vlob_id = any($3::uuid[])
    AND ({})
    AND NOT ({})
ORDER BY vlob_id, version DESC
""".format(
                q_organization_internal_id(Parameter("$1")),
                q_user_can_read_vlob(
                    organization_id=Parameter("$1"),
                    user_id=Parameter("$2"),
                    realm=Query.from_(t_vlob_encryption_revision)
                    .select("realm")
                    .where(t_vlob_encryption_revision._id == Parameter("vlob_encryption_revision")),
                ),
                q_realm_in_maintenance(
                    realm=Query.from_(t_vlob_encryption_revision)
                    .select("realm")
                    .where(t_vlob_encryption_revision._id == Parameter("vlob_encryption_revision"))
                ),
            )

            rows = await conn.fetch(query, organization_id, author.user_id, to_check_dict.keys())

        for vlob_id, version in rows:
            if version != to_check_dict[vlob_id]["version"]:
                changed.append({"vlob_id": vlob_id, "version": version})

        return changed

    async def poll_changes(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID, checkpoint: int
    ) -> Tuple[int, Dict[UUID, int]]:
        async with self.dbh.pool.acquire() as conn, conn.transaction():

            await _check_realm_and_read_access(conn, organization_id, author, realm_id, None)
            query = """
SELECT
    index,
    vlob_id,
    vlob_atom.version
FROM realm_vlob_update
LEFT JOIN vlob_atom ON realm_vlob_update.vlob_atom = vlob_atom._id
WHERE
    realm = ({})
    AND index > $3
ORDER BY index ASC
""".format(
                q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2"))
            )

            ret = await conn.fetch(query, organization_id, realm_id, checkpoint)

        changes_since_checkpoint = {src_id: src_version for _, src_id, src_version in ret}
        new_checkpoint = ret[-1][0] if ret else checkpoint
        return (new_checkpoint, changes_since_checkpoint)

    async def list_versions(
        self, organization_id: OrganizationID, author: DeviceID, vlob_id: UUID
    ) -> Dict[int, Tuple[pendulum.Pendulum, DeviceID]]:

        async with self.dbh.pool.acquire() as conn:
            async with conn.transaction():

                realm_id = await _get_realm_id_from_vlob_id(conn, organization_id, vlob_id)
                await _check_realm_and_read_access(conn, organization_id, author, realm_id, None)

                query = """
SELECT
    version,
    ({}) as author,
    created_on
FROM vlob_atom
WHERE
    organization = ({})
    AND vlob_id = $2
ORDER BY version DESC
""".format(
                    q_device(_id=Parameter("author")).select("device_id"),
                    q_organization_internal_id(Parameter("$1")),
                )
                rows = await conn.fetch(query, organization_id, vlob_id)
                assert rows
        if not rows:
            raise VlobNotFoundError(f"Vlob `{vlob_id}` doesn't exist")

        return {row["version"]: (row["created_on"], row["author"]) for row in rows}

    async def maintenance_get_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        size: int,
    ) -> List[Tuple[UUID, int, bytes]]:
        async with self.dbh.pool.acquire() as conn, conn.transaction():

            await _check_realm_and_maintenance_access(
                conn, organization_id, author, realm_id, encryption_revision
            )

            query = """
WITH cte_to_encrypt AS (
    SELECT vlob_id, version, blob
    FROM vlob_atom
    WHERE vlob_encryption_revision = ({})
),
cte_encrypted AS (
    SELECT vlob_id, version
    FROM vlob_atom
    WHERE vlob_encryption_revision = ({})
)
SELECT
    cte_to_encrypt.vlob_id,
    cte_to_encrypt.version,
    blob
FROM cte_to_encrypt
LEFT JOIN cte_encrypted
ON cte_to_encrypt.vlob_id = cte_encrypted.vlob_id AND cte_to_encrypt.version = cte_encrypted.version
WHERE cte_encrypted.vlob_id IS NULL
LIMIT $4
""".format(
                q_vlob_encryption_revision_internal_id(
                    organization_id=Parameter("$1"),
                    realm_id=Parameter("$2"),
                    encryption_revision=Parameter("$3") - 1,
                ),
                q_vlob_encryption_revision_internal_id(
                    organization_id=Parameter("$1"),
                    realm_id=Parameter("$2"),
                    encryption_revision=Parameter("$3"),
                ),
            )

            rep = await conn.fetch(query, organization_id, realm_id, encryption_revision, size)
            return [(row["vlob_id"], row["version"], row["blob"]) for row in rep]

    async def maintenance_save_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        batch: List[Tuple[UUID, int, bytes]],
    ) -> Tuple[int, int]:
        async with self.dbh.pool.acquire() as conn, conn.transaction():

            await _check_realm_and_maintenance_access(
                conn, organization_id, author, realm_id, encryption_revision
            )
            for vlob_id, version, blob in batch:
                query = """
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
    ({}),
    $3,
    $4,
    $6,
    $7,
    author,
    created_on,
    deleted_on
FROM vlob_atom
WHERE
    organization = ({})
    AND vlob_id = $3
    AND version = $4
ON CONFLICT DO NOTHING
""".format(
                    q_vlob_encryption_revision_internal_id(
                        organization_id=Parameter("$1"),
                        realm_id=Parameter("$2"),
                        encryption_revision=Parameter("$5"),
                    ),
                    q_organization_internal_id(Parameter("$1")),
                )

                await conn.execute(
                    query,
                    organization_id,
                    realm_id,
                    vlob_id,
                    version,
                    encryption_revision,
                    blob,
                    len(blob),
                )

            query = """
SELECT (
    SELECT COUNT(*)
    FROM vlob_atom
    WHERE vlob_encryption_revision = ({})
),
(
    SELECT COUNT(*)
    FROM vlob_atom
    WHERE vlob_encryption_revision = ({})
)
""".format(
                q_vlob_encryption_revision_internal_id(
                    organization_id=Parameter("$1"),
                    realm_id=Parameter("$2"),
                    encryption_revision=Parameter("$3") - 1,
                ),
                q_vlob_encryption_revision_internal_id(
                    organization_id=Parameter("$1"),
                    realm_id=Parameter("$2"),
                    encryption_revision=Parameter("$3"),
                ),
            )

            rep = await conn.fetchrow(query, organization_id, realm_id, encryption_revision)

            return rep[0], rep[1]
