# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

import pendulum
from pypika import Parameter
from triopg.exceptions import UniqueViolationError

from parsec.api.protocol import DeviceID, OrganizationID
from parsec.backend.block import (
    BaseBlockComponent,
    BlockAccessError,
    BlockAlreadyExistsError,
    BlockError,
    BlockInMaintenanceError,
    BlockNotFoundError,
)
from parsec.backend.blockstore import BaseBlockStoreComponent
from parsec.backend.postgresql.handler import PGHandler
from parsec.backend.postgresql.realm_queries.maintenance import RealmNotFoundError, get_realm_status
from parsec.backend.postgresql.tables import (
    q_block,
    q_device_internal_id,
    q_organization_internal_id,
    q_realm,
    q_realm_internal_id,
    q_user_can_read_vlob,
    q_user_can_write_vlob,
    q_user_internal_id,
    t_block,
    t_block_data,
)
from parsec.backend.postgresql.utils import Query, fn_exists
from parsec.backend.vlob import BaseVlobComponent

_q_get_realm_id_from_block_id = (
    q_block(organization_id=Parameter("$1"), block_id=Parameter("$2"))
    .select(q_realm(_id=t_block.realm).select("realm_id"))
    .get_sql()
)


_q_get_block_meta = (
    q_block(organization_id=Parameter("$1"), block_id=Parameter("$2"))
    .select(
        "deleted_on",
        q_user_can_read_vlob(
            user=q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$3")),
            realm=t_block.realm,
        ),
    )
    .get_sql()
)


_q_get_block_write_right_and_unicity = Query.select(
    q_user_can_write_vlob(
        user=q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$2")),
        realm=q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$3")),
    ),
    fn_exists(q_block(organization_id=Parameter("$1"), block_id=Parameter("$4"))),
).get_sql()


_q_insert_block = (
    Query.into(t_block)
    .columns("organization", "block_id", "realm", "author", "size", "created_on")
    .insert(
        q_organization_internal_id(Parameter("$1")),
        Parameter("$2"),
        q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$3")),
        q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$4")),
        Parameter("$5"),
        Parameter("$6"),
    )
    .get_sql()
)


async def _check_realm(conn, organization_id, realm_id):
    try:
        rep = await get_realm_status(conn, organization_id, realm_id)

    except RealmNotFoundError as exc:
        raise BlockNotFoundError(*exc.args) from exc

    if rep["maintenance_type"]:
        raise BlockInMaintenanceError("Data realm is currently under maintenance")


class PGBlockComponent(BaseBlockComponent):
    def __init__(
        self,
        dbh: PGHandler,
        blockstore_component: BaseBlockStoreComponent,
        vlob_component: BaseVlobComponent,
    ):
        self.dbh = dbh
        self._blockstore_component = blockstore_component
        self._vlob_component = vlob_component

    async def read(
        self, organization_id: OrganizationID, author: DeviceID, block_id: UUID
    ) -> bytes:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            realm_id = await conn.fetchval(_q_get_realm_id_from_block_id, organization_id, block_id)
            if not realm_id:
                raise BlockNotFoundError(f"Realm `{realm_id}` doesn't exist")
            await _check_realm(conn, organization_id, realm_id)
            ret = await conn.fetchrow(_q_get_block_meta, organization_id, block_id, author.user_id)
            if not ret or ret[0]:
                raise BlockNotFoundError()

            elif not ret[1]:
                raise BlockAccessError()

        return await self._blockstore_component.read(organization_id, block_id)

    async def create(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        block_id: UUID,
        realm_id: UUID,
        block: bytes,
    ) -> None:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            await _check_realm(conn, organization_id, realm_id)

            # 1) Check access rights and block unicity
            ret = await conn.fetchrow(
                _q_get_block_write_right_and_unicity,
                organization_id,
                author.user_id,
                realm_id,
                block_id,
            )

            if not ret[0]:
                raise BlockAccessError()

            elif ret[1]:
                raise BlockAlreadyExistsError()

            # 2) Upload block data in blockstore under an arbitrary id
            # Given block metadata and block data are stored on different
            # storages, beeing atomic is not easy here :(
            # For instance step 2) can be successful (or can be successful on
            # *some* blockstores in case of a RAID blockstores configuration)
            # but step 4) fails. To avoid deadlock in such case (i.e.
            # blockstores with existing block raise `BlockAlreadyExistsError`)
            # blockstore are idempotent (i.e. if a block id already exists a
            # blockstore return success without any modification).
            await self._blockstore_component.create(organization_id, block_id, block)

            # 3) Insert the block metadata into the database
            ret = await conn.execute(
                _q_insert_block,
                organization_id,
                block_id,
                realm_id,
                author,
                len(block),
                pendulum.now(),
            )

            if ret != "INSERT 0 1":
                raise BlockError(f"Insertion error: {ret}")


_q_get_block_data = (
    Query.from_(t_block_data)
    .select("data")
    .where(t_block_data.organization_id == Parameter("$1"))
    .where(t_block_data.block_id == Parameter("$2"))
).get_sql()


_q_insert_block_data = (
    Query.into(t_block_data)
    .columns("organization_id", "block_id", "data")
    .insert(Parameter("$1"), Parameter("$2"), Parameter("$3"))
    .get_sql()
)


class PGBlockStoreComponent(BaseBlockStoreComponent):
    def __init__(self, dbh: PGHandler):
        self.dbh = dbh

    async def read(self, organization_id: OrganizationID, id: UUID) -> bytes:
        async with self.dbh.pool.acquire() as conn:
            ret = await conn.fetchrow(_q_get_block_data, organization_id, id)
            if not ret:
                raise BlockNotFoundError()

            return ret[0]

    async def create(self, organization_id: OrganizationID, id: UUID, block: bytes) -> None:
        async with self.dbh.pool.acquire() as conn:
            try:
                ret = await conn.execute(_q_insert_block_data, organization_id, id, block)
                if ret != "INSERT 0 1":
                    raise BlockError(f"Insertion error: {ret}")

            except UniqueViolationError:
                # Keep calm and stay idempotent
                pass
