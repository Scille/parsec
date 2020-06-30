# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Tuple

import pendulum

from parsec.api.protocol import DeviceID, OrganizationID, UserID
from parsec.backend.postgresql.handler import PGHandler
from parsec.backend.postgresql.user_queries import (
    query_cancel_device_invitation,
    query_cancel_user_invitation,
    query_claim_device_invitation,
    query_claim_user_invitation,
    query_create_device,
    query_create_device_invitation,
    query_create_user,
    query_create_user_invitation,
    query_find,
    query_find_humans,
    query_get_device_invitation,
    query_get_user,
    query_get_user_invitation,
    query_get_user_with_device,
    query_get_user_with_device_and_trustchain,
    query_get_user_with_devices_and_trustchain,
    query_get_user_with_trustchain,
    query_revoke_user,
)
from parsec.backend.user import (
    BaseUserComponent,
    Device,
    DeviceInvitation,
    GetUserAndDevicesResult,
    HumanFindResultItem,
    Trustchain,
    User,
    UserInvitation,
)


class PGUserComponent(BaseUserComponent):
    def __init__(self, dbh: PGHandler, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dbh = dbh

    async def create_user(
        self, organization_id: OrganizationID, user: User, first_device: Device
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await query_create_user(conn, organization_id, user, first_device)

    async def create_device(
        self, organization_id: OrganizationID, device: Device, encrypted_answer: bytes = b""
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await query_create_device(conn, organization_id, device, encrypted_answer)

    async def get_user(self, organization_id: OrganizationID, user_id: UserID) -> User:
        async with self.dbh.pool.acquire() as conn:
            return await query_get_user(conn, organization_id, user_id)

    async def get_user_with_trustchain(
        self, organization_id: OrganizationID, user_id: UserID
    ) -> Tuple[User, Trustchain]:
        async with self.dbh.pool.acquire() as conn:
            return await query_get_user_with_trustchain(conn, organization_id, user_id)

    async def get_user_with_device_and_trustchain(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> Tuple[User, Device, Trustchain]:
        async with self.dbh.pool.acquire() as conn:
            return await query_get_user_with_device_and_trustchain(conn, organization_id, device_id)

    async def get_user_with_devices_and_trustchain(
        self, organization_id: OrganizationID, user_id: UserID, redacted: bool = False
    ) -> GetUserAndDevicesResult:
        async with self.dbh.pool.acquire() as conn:
            return await query_get_user_with_devices_and_trustchain(
                conn, organization_id, user_id, redacted=redacted
            )

    async def get_user_with_device(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> Tuple[User, Device]:
        async with self.dbh.pool.acquire() as conn:
            return await query_get_user_with_device(conn, organization_id, device_id)

    async def find(
        self,
        organization_id: OrganizationID,
        query: str = None,
        page: int = 1,
        per_page: int = 100,
        omit_revoked: bool = False,
    ) -> Tuple[List[UserID], int]:
        async with self.dbh.pool.acquire() as conn:
            return await query_find(conn, organization_id, query, page, per_page, omit_revoked)

    async def find_humans(
        self,
        organization_id: OrganizationID,
        query: str = None,
        page: int = 1,
        per_page: int = 100,
        omit_revoked: bool = False,
        omit_non_human: bool = False,
    ) -> Tuple[List[HumanFindResultItem], int]:
        async with self.dbh.pool.acquire() as conn:
            return await query_find_humans(
                conn, organization_id, query, page, per_page, omit_revoked, omit_non_human
            )

    async def create_user_invitation(
        self, organization_id: OrganizationID, invitation: UserInvitation
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await query_create_user_invitation(conn, organization_id, invitation)

    async def get_user_invitation(
        self, organization_id: OrganizationID, user_id: UserID
    ) -> UserInvitation:
        async with self.dbh.pool.acquire() as conn:
            return await query_get_user_invitation(conn, organization_id, user_id)

    async def claim_user_invitation(
        self, organization_id: OrganizationID, user_id: UserID, encrypted_claim: bytes = b""
    ) -> UserInvitation:
        async with self.dbh.pool.acquire() as conn:
            return await query_claim_user_invitation(
                conn, organization_id, user_id, encrypted_claim
            )

    async def cancel_user_invitation(
        self, organization_id: OrganizationID, user_id: UserID
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await query_cancel_user_invitation(conn, organization_id, user_id)

    async def create_device_invitation(
        self, organization_id: OrganizationID, invitation: DeviceInvitation
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await query_create_device_invitation(conn, organization_id, invitation)

    async def get_device_invitation(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> DeviceInvitation:
        async with self.dbh.pool.acquire() as conn:
            return await query_get_device_invitation(conn, organization_id, device_id)

    async def claim_device_invitation(
        self, organization_id: OrganizationID, device_id: DeviceID, encrypted_claim: bytes = b""
    ) -> DeviceInvitation:
        async with self.dbh.pool.acquire() as conn:
            return await query_claim_device_invitation(
                conn, organization_id, device_id, encrypted_claim
            )

    async def cancel_device_invitation(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await query_cancel_device_invitation(conn, organization_id, device_id)

    async def revoke_user(
        self,
        organization_id: OrganizationID,
        user_id: UserID,
        revoked_user_certificate: bytes,
        revoked_user_certifier: DeviceID,
        revoked_on: pendulum.Pendulum = None,
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            return await query_revoke_user(
                conn,
                organization_id,
                user_id,
                revoked_user_certificate,
                revoked_user_certifier,
                revoked_on,
            )
