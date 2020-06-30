# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional

from pendulum import Pendulum
from pypika import Parameter
from pypika import functions as fn
from triopg import UniqueViolationError

from parsec.api.protocol import OrganizationID
from parsec.backend.organization import (
    BaseOrganizationComponent,
    Organization,
    OrganizationAlreadyBootstrappedError,
    OrganizationAlreadyExistsError,
    OrganizationError,
    OrganizationFirstUserCreationError,
    OrganizationInvalidBootstrapTokenError,
    OrganizationNotFoundError,
    OrganizationStats,
)
from parsec.backend.postgresql.handler import PGHandler
from parsec.backend.postgresql.tables import (
    q_organization,
    q_organization_internal_id,
    t_block,
    t_organization,
    t_user,
    t_vlob_atom,
)
from parsec.backend.postgresql.user_queries.create import _create_user
from parsec.backend.postgresql.utils import Query
from parsec.backend.user import Device, User, UserError
from parsec.crypto import VerifyKey

_q_insert_organization = (
    (
        Query.into(t_organization)
        .columns("organization_id", "bootstrap_token", "expiration_date")
        .insert(Parameter("$1"), Parameter("$2"), Parameter("$3"))
        .get_sql()
    )
    + """
ON CONFLICT (organization_id) DO
    UPDATE SET
        bootstrap_token = EXCLUDED.bootstrap_token,
        expiration_date = EXCLUDED.expiration_date
    WHERE organization.root_verify_key is NULL
"""
)


_q_get_organization = (
    q_organization(Parameter("$1"))
    .select("bootstrap_token", "root_verify_key", "expiration_date")
    .get_sql()
)


_q_bootstrap_organization = (
    Query.update(t_organization)
    .where(
        (t_organization.organization_id == Parameter("$1"))
        & (t_organization.bootstrap_token == Parameter("$2"))
        & (t_organization.root_verify_key.isnull())
    )
    .set(t_organization.root_verify_key, Parameter("$3"))
    .get_sql()
)


_q_get_stats = Query.select(
    Query.from_(t_user)
    .where(t_user.organization == q_organization_internal_id(Parameter("$1")))
    .select(fn.Count("*"))
    .as_("users"),
    Query.from_(t_vlob_atom)
    .where(t_vlob_atom.organization == q_organization_internal_id(Parameter("$1")))
    .select(fn.Coalesce(fn.Sum(t_vlob_atom.size), 0))
    .as_("metadata_size"),
    Query.from_(t_block)
    .where(t_block.organization == q_organization_internal_id(Parameter("$1")))
    .select(fn.Coalesce(fn.Sum(t_block.size), 0))
    .as_("data_size"),
).get_sql()


_q_update_organisation_expiration_date = (
    Query.update(t_organization)
    .where((t_organization.organization_id == Parameter("$1")))
    .set(t_organization.expiration_date, Parameter("$2"))
    .get_sql()
)


class PGOrganizationComponent(BaseOrganizationComponent):
    def __init__(self, dbh: PGHandler, **kwargs):
        super().__init__(**kwargs)
        self.dbh = dbh

    async def create(
        self, id: OrganizationID, bootstrap_token: str, expiration_date: Optional[Pendulum] = None
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            try:
                result = await conn.execute(
                    _q_insert_organization, id, bootstrap_token, expiration_date
                )
            except UniqueViolationError:
                raise OrganizationAlreadyExistsError()

            if result != "INSERT 0 1":
                raise OrganizationAlreadyExistsError()

    async def get(self, id: OrganizationID) -> Organization:
        async with self.dbh.pool.acquire() as conn:
            return await self._get(conn, id)

    @staticmethod
    async def _get(conn, id: OrganizationID) -> Organization:
        data = await conn.fetchrow(_q_get_organization, id)
        if not data:
            raise OrganizationNotFoundError()

        rvk = VerifyKey(data[1]) if data[1] else None
        return Organization(
            organization_id=id,
            bootstrap_token=data[0],
            root_verify_key=rvk,
            expiration_date=data[2],
        )

    async def bootstrap(
        self,
        id: OrganizationID,
        user: User,
        first_device: Device,
        bootstrap_token: str,
        root_verify_key: VerifyKey,
    ) -> None:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            organization = await self._get(conn, id)

            if organization.is_bootstrapped():
                raise OrganizationAlreadyBootstrappedError()

            if organization.bootstrap_token != bootstrap_token:
                raise OrganizationInvalidBootstrapTokenError()

            try:
                await _create_user(conn, id, user, first_device)
            except UserError as exc:
                raise OrganizationFirstUserCreationError(exc) from exc

            result = await conn.execute(
                _q_bootstrap_organization, id, bootstrap_token, root_verify_key.encode()
            )

            if result != "UPDATE 1":
                raise OrganizationError(f"Update error: {result}")

    async def stats(self, id: OrganizationID) -> OrganizationStats:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            await self._get(conn, id)  # Check organization exists
            result = await conn.fetchrow(_q_get_stats, id)
        return OrganizationStats(
            users=result["users"],
            data_size=result["data_size"],
            metadata_size=result["metadata_size"],
        )

    async def set_expiration_date(
        self, id: OrganizationID, expiration_date: Pendulum = None
    ) -> None:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            result = await conn.execute(_q_update_organisation_expiration_date, id, expiration_date)

            if result == "UPDATE 0":
                raise OrganizationNotFoundError

            if result != "UPDATE 1":
                raise OrganizationError(f"Update error: {result}")
