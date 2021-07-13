# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from typing import Optional, Union

from functools import lru_cache
from pendulum import DateTime
from triopg import UniqueViolationError

from parsec.api.protocol import OrganizationID
from parsec.crypto import VerifyKey
from parsec.backend.events import BackendEvent
from parsec.backend.user import UserError, User, Device
from parsec.backend.utils import UnsetType, Unset
from parsec.backend.organization import (
    BaseOrganizationComponent,
    OrganizationStats,
    Organization,
    OrganizationError,
    OrganizationAlreadyExistsError,
    OrganizationInvalidBootstrapTokenError,
    OrganizationAlreadyBootstrappedError,
    OrganizationNotFoundError,
    OrganizationFirstUserCreationError,
)
from parsec.backend.postgresql.handler import PGHandler
from parsec.backend.postgresql.user_queries.create import _create_user
from parsec.backend.postgresql.utils import Q, q_organization_internal_id
from parsec.backend.postgresql.handler import send_signal


_q_insert_organization = Q(
    """
INSERT INTO organization (organization_id, bootstrap_token, expiration_date, user_profile_outsider_allowed, active_users_limit)
VALUES ($organization_id, $bootstrap_token, $expiration_date, FALSE, $active_users_limit)
ON CONFLICT (organization_id) DO
    UPDATE SET
        bootstrap_token = EXCLUDED.bootstrap_token,
        expiration_date = EXCLUDED.expiration_date
    WHERE organization.root_verify_key is NULL
"""
)


_q_get_organization = Q(
    """
SELECT bootstrap_token, root_verify_key, expiration_date, user_profile_outsider_allowed, active_users_limit
FROM organization
WHERE organization_id = $organization_id
"""
)


_q_bootstrap_organization = Q(
    """
UPDATE organization
SET root_verify_key = $root_verify_key
WHERE
    organization_id = $organization_id
    AND bootstrap_token = $bootstrap_token
    AND root_verify_key IS NULL
"""
)


_q_get_stats = Q(
    f"""
SELECT
    (
        SELECT COUNT(*)
        FROM user_
        WHERE user_.organization = { q_organization_internal_id("$organization_id") }
    ) users,
    (
        SELECT COUNT(*)
        FROM realm
        WHERE realm.organization = { q_organization_internal_id("$organization_id") }
    ) workspaces,
    (
        SELECT COALESCE(SUM(size), 0)
        FROM vlob_atom
        WHERE
            organization = { q_organization_internal_id("$organization_id") }
    ) metadata_size,
    (
        SELECT COALESCE(SUM(size), 0)
        FROM block
        WHERE
            organization = { q_organization_internal_id("$organization_id") }
    ) data_size
"""
)


_q_update_organisation_expiration_date = Q(
    """
UPDATE organization
SET expiration_date = $expiration_date
WHERE organization_id = $organization_id
"""
)

_q_check_users_limit = Q(
    f"""
    SELECT
        organization._id
    FROM organization
    LEFT JOIN
        user_ on user_.organization = organization._id
    WHERE
        organization = { q_organization_internal_id("$organization_id") }
    GROUP BY organization._id
    HAVING active_users_limit is NULL OR count(user_) < active_users_limit;
"""
)


@lru_cache()
def _q_update_factory(
    with_expiration_date: bool,
    with_user_profile_outsider_allowed: bool,
    with_active_users_limit: bool,
):
    fields = []
    if with_expiration_date:
        fields.append("expiration_date = $expiration_date")
    if with_user_profile_outsider_allowed:
        fields.append("user_profile_outsider_allowed = $user_profile_outsider_allowed")
    if with_active_users_limit:
        fields.append("active_users_limit = $active_users_limit")

    return Q(
        f"""
            UPDATE organization
            SET
            { ", ".join(fields) }
            WHERE organization_id = $organization_id
        """
    )


class PGOrganizationComponent(BaseOrganizationComponent):
    def __init__(self, dbh: PGHandler, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dbh = dbh

    async def create(
        self,
        id: OrganizationID,
        bootstrap_token: str,
        expiration_date: Optional[DateTime] = None,
        active_users_limit: Optional[int] = None,
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            try:
                result = await conn.execute(
                    *_q_insert_organization(
                        organization_id=id,
                        bootstrap_token=bootstrap_token,
                        expiration_date=expiration_date,
                        active_users_limit=active_users_limit,
                    )
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
        data = await conn.fetchrow(*_q_get_organization(organization_id=id))
        if not data:
            raise OrganizationNotFoundError()

        rvk = VerifyKey(data[1]) if data[1] else None
        return Organization(
            organization_id=id,
            bootstrap_token=data[0],
            root_verify_key=rvk,
            expiration_date=data[2],
            user_profile_outsider_allowed=data[3],
            active_users_limit=data[4],
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
                *_q_bootstrap_organization(
                    organization_id=id,
                    bootstrap_token=bootstrap_token,
                    root_verify_key=root_verify_key.encode(),
                )
            )

            if result != "UPDATE 1":
                raise OrganizationError(f"Update error: {result}")

    async def stats(self, id: OrganizationID) -> OrganizationStats:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            await self._get(conn, id)  # Check organization exists
            result = await conn.fetchrow(*_q_get_stats(organization_id=id))
        return OrganizationStats(
            users=result["users"],
            data_size=result["data_size"],
            metadata_size=result["metadata_size"],
            workspaces=result["workspaces"],
        )

    async def update(
        self,
        id: OrganizationID,
        expiration_date: Union[UnsetType, Optional[DateTime]] = Unset,
        user_profile_outsider_allowed: Union[UnsetType, bool] = Unset,
        active_users_limit: Union[UnsetType, int] = Unset,
    ) -> None:
        """
        Raises:
            OrganizationNotFoundError
            OrganizationError
        """
        fields: dict = {}

        with_expiration_date = expiration_date is not Unset
        with_user_profile_outsider_allowed = user_profile_outsider_allowed is not Unset
        with_active_users_limit = active_users_limit is not Unset

        if with_expiration_date:
            fields["expiration_date"] = expiration_date
        if with_user_profile_outsider_allowed:
            fields["user_profile_outsider_allowed"] = user_profile_outsider_allowed
        if with_active_users_limit:
            fields["active_users_limit"] = active_users_limit

        q = _q_update_factory(
            with_expiration_date=with_expiration_date,
            with_user_profile_outsider_allowed=with_user_profile_outsider_allowed,
            with_active_users_limit=with_active_users_limit,
        )

        async with self.dbh.pool.acquire() as conn, conn.transaction():
            result = await conn.execute(*q(organization_id=id, **fields))

            if result == "UPDATE 0":
                raise OrganizationNotFoundError

            if result != "UPDATE 1":
                raise OrganizationError(f"Update error: {result}")

            if isinstance(expiration_date, DateTime) and expiration_date <= DateTime.now():
                await send_signal(conn, BackendEvent.ORGANIZATION_EXPIRED, organization_id=id)
