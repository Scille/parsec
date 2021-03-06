# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

from parsec.api.protocol import UserID, DeviceID, OrganizationID
from parsec.backend.stats import (
    BaseStatsComponent,
    OrganizationStats,
    UserStats,
    DeviceStats,
    RealmStats,
)
from parsec.backend.postgresql.handler import PGHandler
from parsec.backend.postgresql.utils import (
    Q,
    q_organization_internal_id,
    q_user_internal_id,
    q_device_internal_id,
    q_realm_internal_id,
)


# _q_get_organization_stats = Q(
#     f"""
#     SELECT
#         crunched_on,
#         users_count,
#         blocks_size,
#         blocks_count,
#         vlobs_size,
#         vlobs_count
#     FROM organization_stats
#     WHERE
#         organization = { q_organization_internal_id("$organization_id") }
#         AND crunched_on > (now() - (interval '1 second' * $max_age))
#     """
# )


# _q_crunch_organization_stats = Q(
#     f"""
# INSERT INTO organization_stats(
#     organization,
#     crunched_on,
#     users_count,
#     blocks_size,
#     blocks_count,
#     vlobs_size,
#     vlobs_count
# )
# VALUES(
#     { q_organization_internal_id("$organization_id") },
#     now(),
#     (
#         SELECT COUNT(*)
#         FROM user_
#         WHERE user_.organization = { q_organization_internal_id("$organization_id") }
#     ),
#     (
#         SELECT COALESCE(SUM(size), 0)
#         FROM block
#         WHERE
#             organization = { q_organization_internal_id("$organization_id") }
#     ),
#     (
#         SELECT COUNT(*)
#         FROM block
#         WHERE
#             organization = { q_organization_internal_id("$organization_id") }
#     ),
#     (
#         SELECT COALESCE(SUM(size), 0)
#         FROM vlob_atom
#         WHERE
#             organization = { q_organization_internal_id("$organization_id") }
#     ),
#     (
#         SELECT COUNT(*)
#         FROM vlob_atom
#         WHERE
#             organization = { q_organization_internal_id("$organization_id") }
#     )
# )
# ON CONFLICT (organization)
# DO UPDATE SET
#     crunched_on = EXCLUDED.crunched_on,
#     users_count = EXCLUDED.users_count,
#     blocks_size = EXCLUDED.blocks_size,
#     blocks_count = EXCLUDED.blocks_count,
#     vlobs_size = EXCLUDED.vlobs_size,
#     vlobs_count = EXCLUDED.vlobs_count
# RETURNING
#     crunched_on,
#     users_count,
#     blocks_size,
#     blocks_count,
#     vlobs_size,
#     vlobs_count
# """
# )


# _q_update_device_stats = Q(
#     f"""
# INSERT INTO device_stats(
#     device,
#     last_connected_on,
#     blocks_size,
#     blocks_count,
#     vlobs_size,
#     vlobs_count
# )
# VALUES(
#     { q_device_internal_id(organization_id="$organization_id", device_id="$device_id") },
#     now(),
#     $blocks_size,
#     $blocks_count,
#     $vlobs_size,
#     $vlobs_count
# )
# ON CONFLICT (device)
# DO UPDATE SET
#     last_connected_on = last_connected_on + EXCLUDED.last_connected_on
#     blocks_size = blocks_size + EXCLUDED.blocks_size,
#     blocks_count = blocks_count + EXCLUDED.blocks_count,
#     vlobs_size = vlobs_size + EXCLUDED.vlobs_size,
#     vlobs_count = vlobs_count + EXCLUDED.vlobs_count
# """
# )


# _q_get_organization_stats = Q(
#     f"""
# SELECT
#     MAX(last_connected_on) last_connected_on,
#     (
#         SELECT COUNT(*)
#         FROM user_
#         WHERE user_.organization = { q_organization_internal_id("$organization_id") }
#     ) users_count,
#     SUM(blocks_size) blocks_size,
#     SUM(blocks_count) blocks_count,
#     SUM(vlobs_size) vlobs_size,
#     SUM(vlobs_count) vlobs_count
# FROM device_stats
# LEFT JOIN device
# ON
#     device_stats.device = device._id
# WHERE
#     device.organization = { q_organization_internal_id("$organization_id") }
# """
# )


# _q_get_user_stats = Q(
#     f"""
# SELECT
#     MAX(last_connected_on) last_connected_on,
#     SUM(*) devices_count,
#     SUM(blocks_size) blocks_size,
#     SUM(blocks_count) blocks_count,
#     SUM(vlobs_size) vlobs_size,
#     SUM(vlobs_count) vlobs_count
# FROM device_stats
# LEFT JOIN device
# ON
#     device_stats.device = device._id
# WHERE
#     device.user_ = { q_user_internal_id(organization_id="$organization_id", user_id="$user_id") }
# """
# )


# _q_get_device_stats = Q(
#     f"""
# SELECT
#     last_connected_on,
#     blocks_size,
#     blocks_count,
#     vlobs_size,
#     vlobs_count
# FROM device_stats
# WHERE
#     _id = { q_device_internal_id(organization_id="$organization_id", device_id="$device_id") }
# """
# )


# @query(in_transaction=True)
# async def query_update_device_stats(
#     conn,
#     organization_id: OrganizationID,
#     device_id: DeviceID,
#     blocks_size: int = 0,
#     blocks_count: int = 0,
#     vlobs_size: int = 0,
#     vlobs_count: int = 0,
# ) -> None:
#     await conn.execute(
#         *_q_update_device_stats(
#             organization_id=organization_id,
#             device_id=device_id,
#             blocks_size=blocks_size,
#             blocks_count=blocks_count,
#             vlobs_size=vlobs_size,
#             vlobs_count=vlobs_count,
#         )
#     )


_q_update_device_last_connected_on = Q(
    f"""
UPDATE device
SET last_connected_on = now()
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND device_id = $device_id
"""
)

# _q_update_device_last_connected_on = Q(
#     f"""
# INSERT INTO device_last_connection(
#     device,
#     last_connected_on,
# )
# VALUES(
#     { q_device_internal_id(organization_id="$organization_id", device_id="$device_id") },
#     now(),
# )
# ON CONFLICT (device)
# DO UPDATE SET
#     last_connected_on = EXCLUDED.last_connected_on
# """
# )


_q_get_organization_stats = Q(
    f"""
SELECT
    MAX(device.last_connected_on) last_connected_on,
    COUNT(DISTINCT device._id) devices_count,
    COUNT(DISTINCT device.user_) users_count,
    SUM(vlobs_size) vlobs_size,
    SUM(vlobs_count) vlobs_count,
    SUM(blocks_size) blocks_size,
    SUM(blocks_count) blocks_count
FROM
    device_realm_stats LEFT JOIN device
    ON device_realm_stats.device = device._id
WHERE
    device.organization = { q_organization_internal_id("$organization_id") }
"""
)


_q_get_user_stats = Q(
    f"""
SELECT
    MAX(device.last_connected_on) last_connected_on,
    COUNT(DISTINCT device._id) devices_count,
    SUM(vlobs_size) vlobs_size,
    SUM(vlobs_count) vlobs_count,
    SUM(blocks_size) blocks_size,
    SUM(blocks_count) blocks_count
FROM
    device_realm_stats LEFT JOIN device
    ON device_realm_stats.device = device._id
WHERE
    device.organization = { q_organization_internal_id("$organization_id") }
    device.user_ = { q_user_internal_id(organization_id="$organization_id", user_id="$user_id") }
"""
)


_q_get_device_stats = Q(
    f"""
SELECT
    last_connected_on,
    SUM(vlobs_size) vlobs_size,
    SUM(vlobs_count) vlobs_count,
    SUM(blocks_size) blocks_size,
    SUM(blocks_count) blocks_count
FROM
    device_realm_stats LEFT JOIN device
    ON device_realm_stats.device = device._id
WHERE
    device = { q_device_internal_id(organization_id="$organization_id", device_id="$device_id") }
"""
)


_q_get_realm_stats = Q(
    f"""
SELECT
    SUM(vlobs_size) vlobs_size,
    SUM(vlobs_count) vlobs_count,
    SUM(blocks_size) blocks_size,
    SUM(blocks_count) blocks_count
FROM device_realm_stats
WHERE
    realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
"""
)


class PGStatsComponent(BaseStatsComponent):
    def __init__(self, dbh: PGHandler, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dbh = dbh

    async def organization_stats(self, organization_id: OrganizationID) -> OrganizationStats:
        async with self.dbh.pool.acquire() as conn:
            row = await conn.fetchrow(*_q_get_organization_stats(organization_id=organization_id))
            return OrganizationStats(
                last_connected_on=row["last_connected_on"],
                last_connected_by=row["last_connected_by"],
                users_count=row["users_count"],
                devices_count=row["devices_count"],
                blocks_size=row["blocks_size"],
                blocks_count=row["blocks_count"],
                vlobs_size=row["vlobs_size"],
                vlobs_count=row["vlobs_count"],
            )

    async def user_stats(self, organization_id: OrganizationID, user_id: UserID) -> UserStats:
        async with self.dbh.pool.acquire() as conn:
            row = await conn.fetchrow(
                *_q_get_user_stats(organization_id=organization_id, user_id=user_id)
            )
            return UserStats(
                last_connected_on=row["last_connected_on"],
                last_connected_by=row["last_connected_by"],
                devices_count=row["devices_count"],
                blocks_size=row["blocks_size"],
                blocks_count=row["blocks_count"],
                vlobs_size=row["vlobs_size"],
                vlobs_count=row["vlobs_count"],
            )

    async def device_stats(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> DeviceStats:
        async with self.dbh.pool.acquire() as conn:
            row = await conn.fetchrow(
                *_q_get_device_stats(organization_id=organization_id, device_id=device_id)
            )
            return DeviceStats(
                last_connected_on=row["last_connected_on"],
                blocks_size=row["blocks_size"],
                blocks_count=row["blocks_count"],
                vlobs_size=row["vlobs_size"],
                vlobs_count=row["vlobs_count"],
            )

    async def realm_stats(self, organization_id: OrganizationID, realm_id: UUID) -> RealmStats:
        async with self.dbh.pool.acquire() as conn:
            row = await conn.fetchrow(
                *_q_get_realm_stats(organization_id=organization_id, realm_id=realm_id)
            )
            return RealmStats(
                blocks_size=row["blocks_size"],
                blocks_count=row["blocks_count"],
                vlobs_size=row["vlobs_size"],
                vlobs_count=row["vlobs_count"],
            )

    async def update_last_connection(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await conn.execute(
                *_q_update_device_last_connected_on(
                    organization_id=organization_id, device_id=device_id
                )
            )
