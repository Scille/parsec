# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import attr
from uuid import UUID
from typing import Optional
from pendulum import DateTime

from parsec.api.protocol import UserID, DeviceID, OrganizationID


@attr.s(slots=True, frozen=True, auto_attribs=True)
class OrganizationStats:
    # No last connection is possible if the organization hasn't been bootstrapped yet
    last_connected_on: Optional[DateTime]
    last_connected_by: Optional[DeviceID]
    users_count: int
    devices_count: int
    vlobs_size: int
    vlobs_count: int
    blocks_size: int
    blocks_count: int


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserStats:
    last_connected_on: DateTime
    last_connected_by: DeviceID
    devices_count: int
    vlobs_size: int
    vlobs_count: int
    blocks_size: int
    blocks_count: int


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceStats:
    last_connected_on: DateTime
    vlobs_size: int
    vlobs_count: int
    blocks_size: int
    blocks_count: int


@attr.s(slots=True, frozen=True, auto_attribs=True)
class RealmStats:
    vlobs_size: int
    vlobs_count: int
    blocks_size: int
    blocks_count: int


class StatsError(Exception):
    pass


class StatsNotFoundError(StatsError):
    pass


class StatsAccessError(StatsError):
    pass


class BaseStatsComponent:
    async def organization_stats(self, organization_id: OrganizationID) -> OrganizationStats:
        """
        Raises:
            StatsNotFoundError
        """
        raise NotImplementedError

    async def user_stats(self, organization_id: OrganizationID, user: UserID) -> UserStats:
        """
        Raises:
            StatsNotFoundError
        """
        raise NotImplementedError

    async def device_stats(self, organization_id: OrganizationID, device: DeviceID) -> DeviceStats:
        """
        Raises:
            StatsNotFoundError
        """
        raise NotImplementedError

    async def realm_stats(
        self, organization_id: OrganizationID, realm_id: UUID, check_access: Optional[UserID] = None
    ) -> RealmStats:
        """
        Raises:
            StatsNotFoundError
            StatsAccessError
        """
        raise NotImplementedError

    async def update_last_connection(
        self, organization_id: OrganizationID, device: DeviceID, now: DateTime = None
    ) -> None:
        """
        Raises: Nothing !
        """
        raise NotImplementedError
