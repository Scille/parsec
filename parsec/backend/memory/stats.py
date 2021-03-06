# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional, Dict, Tuple
from uuid import UUID
from pendulum import DateTime, now as pendulum_now

from parsec.api.protocol import UserID, DeviceID, OrganizationID
from parsec.backend.stats import (
    BaseStatsComponent,
    OrganizationStats,
    UserStats,
    DeviceStats,
    RealmStats,
    StatsNotFoundError,
    StatsAccessError,
)
from parsec.backend import memory


class MemoryStatsComponent(BaseStatsComponent):
    def __init__(self):
        self._user_component: "memory.MemoryUserComponent"  # Defined in `register_components`
        self._vlob_component: "memory.MemoryVlobComponent"  # Defined in `register_components`
        self._block_component: "memory.MemoryBlockComponent"  # Defined in `register_components`
        self._last_connections: Dict[Tuple[OrganizationID, DeviceID], DateTime] = {}

    def register_components(
        self,
        user: "memory.MemoryUserComponent",
        vlob: "memory.MemoryVlobComponent",
        block: "memory.MemoryBlockComponent",
        realm: "memory.MemoryRealmComponent",
        **other_components,
    ):
        self._user_component = user
        self._vlob_component = vlob
        self._block_component = block
        self._realm_component = realm

    # Stats needs to access multiple unrelated data. On the postgreSQL backend
    # we just access the corresponding tables, but here we have to connect
    # with other components. So for simplicity sake we just access components'
    # internals.

    def _ensure_exists(
        self,
        organization_id: OrganizationID,
        user_id: Optional[UserID] = None,
        device_id: Optional[DeviceID] = None,
        realm_id: Optional[UUID] = None,
    ) -> None:
        org = self._user_component._organizations.get(organization_id)
        if not org:
            raise StatsNotFoundError(f"Organization `{organization_id}` doesn't exist")

        if device_id:
            assert user_id is None
            user_id = device_id.user_id
        if user_id is not None:
            user_devices = org.devices.get(user_id)
            if not user_devices:
                raise StatsNotFoundError(f"User `{user_id}` doesn't exist")
            if device_id is not None:
                if device_id.device_name not in user_devices:
                    raise StatsNotFoundError(f"Device `{device_id}` doesn't exist")

        if realm_id is not None:
            if (organization_id, realm_id) not in self._realm_component._realms:
                raise StatsNotFoundError(f"Realm `{realm_id}` doesn't exist")

    def _check_realm_access(self, organization_id: OrganizationID, realm_id: UUID, user_id: UserID):
        # Don't catch KeyError given `_ensure_exists` should have been called before
        realm = self._realm_component._realms[(organization_id, realm_id)]
        if realm.roles.get(user_id) is None:
            raise StatsAccessError(f"Not allowed to access stats for realm {realm_id}")

    def _crunch_user_stats(self, organization_id: OrganizationID, user_id: UserID = None) -> Dict:
        org = self._user_component._organizations[organization_id]
        if user_id is not None:
            return {"devices_count": len(org.devices[user_id])}
        else:
            return {
                "users_count": len(org.users),
                "devices_count": sum([len(d) for d in org.devices.values()]),
            }

    def _crunch_vlob_stats(
        self,
        organization_id: OrganizationID,
        user_id: UserID = None,
        device_id: DeviceID = None,
        realm_id: UUID = None,
    ) -> Dict:
        vlobs_count = 0
        vlobs_size = 0
        for (org_id, _), vlob in self._vlob_component._vlobs.items():
            if org_id != organization_id:
                continue
            for data, author, _ in vlob.data:
                if user_id is not None and author.user_id != user_id:
                    continue
                if device_id is not None and author != device_id:
                    continue
                if realm_id is not None and vlob.realm_id != realm_id:
                    continue
                vlobs_count += 1
                vlobs_size += len(data)
        return {"vlobs_size": vlobs_size, "vlobs_count": vlobs_count}

    def _crunch_block_stats(
        self,
        organization_id: OrganizationID,
        user_id: UserID = None,
        device_id: DeviceID = None,
        realm_id: UUID = None,
    ) -> Dict:
        blocks_count = 0
        blocks_size = 0
        for (org_id, _), meta in self._block_component._blockmetas.items():
            if org_id != organization_id:
                continue
            if user_id is not None and meta.author.user_id != user_id:
                continue
            if device_id is not None and meta.author != device_id:
                continue
            if realm_id is not None and meta.realm_id != realm_id:
                continue
            blocks_count += 1
            blocks_size += meta.size
        return {"blocks_count": blocks_count, "blocks_size": blocks_size}

    async def organization_stats(self, organization_id: OrganizationID) -> OrganizationStats:
        self._ensure_exists(organization_id=organization_id)
        try:
            last_connected_on, last_connected_by = max(
                [
                    (dt, devid)
                    for (orgid, devid), dt in self._last_connections.items()
                    if orgid == organization_id
                ],
                key=lambda x: x[0],
            )
        except ValueError:
            # Ending up here means the organization hasn't been bootstrapped
            last_connected_on = last_connected_by = None

        return OrganizationStats(
            last_connected_on=last_connected_on,
            last_connected_by=last_connected_by,
            **self._crunch_user_stats(organization_id=organization_id),
            **self._crunch_vlob_stats(organization_id=organization_id),
            **self._crunch_block_stats(organization_id=organization_id),
        )

    async def user_stats(self, organization_id: OrganizationID, user_id: UserID) -> UserStats:
        self._ensure_exists(organization_id=organization_id, user_id=user_id)
        # last connection info is initialized on user creation so no risk to this empty
        last_connected_on, last_connected_by = max(
            [
                (dt, devid)
                for (orgid, devid), dt in self._last_connections.items()
                if orgid == organization_id and devid.user_id == user_id
            ],
            key=lambda x: x[0],
        )
        return UserStats(
            last_connected_on=last_connected_on,
            last_connected_by=last_connected_by,
            **self._crunch_user_stats(organization_id=organization_id, user_id=user_id),
            **self._crunch_vlob_stats(organization_id=organization_id, user_id=user_id),
            **self._crunch_block_stats(organization_id=organization_id, user_id=user_id),
        )

    async def device_stats(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> DeviceStats:
        self._ensure_exists(organization_id=organization_id, device_id=device_id)
        return DeviceStats(
            # `last_connected_on` is initialized on device creation so no risk to have KeyError
            last_connected_on=self._last_connections[organization_id, device_id],
            **self._crunch_vlob_stats(organization_id=organization_id, device_id=device_id),
            **self._crunch_block_stats(organization_id=organization_id, device_id=device_id),
        )

    async def realm_stats(
        self, organization_id: OrganizationID, realm_id: UUID, check_access: Optional[UserID] = None
    ) -> RealmStats:
        self._ensure_exists(organization_id=organization_id, realm_id=realm_id)
        if check_access is not None:
            self._check_realm_access(
                organization_id=organization_id, realm_id=realm_id, user_id=check_access
            )
        return RealmStats(
            **self._crunch_vlob_stats(organization_id=organization_id, realm_id=realm_id),
            **self._crunch_block_stats(organization_id=organization_id, realm_id=realm_id),
        )

    async def update_last_connection(
        self, organization_id: OrganizationID, device_id: DeviceID, now: DateTime = None
    ) -> None:
        self._last_connections[organization_id, device_id] = now or pendulum_now()
