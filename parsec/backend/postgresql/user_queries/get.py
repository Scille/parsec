# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from typing import Tuple, Optional

from parsec.api.protocol import OrganizationID, UserID, DeviceID, HumanHandle
from parsec.backend.user import User, Device, Trustchain, UserNotFoundError, GetUserAndDevicesResult
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_organization_internal_id,
    q_device,
    q_user_internal_id,
    q_human,
    STR_TO_USER_PROFILE,
)


_q_get_user = Q(
    f"""
SELECT
    { q_human(_id="user_.human", select="email") } as human_email,
    { q_human(_id="user_.human", select="label") } as human_label,
    profile,
    user_certificate,
    redacted_user_certificate,
    { q_device(select="device_id", _id="user_.user_certifier") } as user_certifier,
    created_on,
    revoked_on,
    revoked_user_certificate,
    { q_device(select="device_id", _id="user_.revoked_user_certifier") } as revoked_user_certifier
FROM user_
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND user_id = $user_id
"""
)


_q_get_device = Q(
    f"""
SELECT
    device_label,
    device_certificate,
    redacted_device_certificate,
    { q_device(table_alias="d", select="d.device_id", _id="device.device_certifier") } as device_certifier,
    created_on
FROM device
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND device_id = $device_id
"""
)


_q_get_user_devices = Q(
    f"""
SELECT
    device_id,
    device_label,
    device_certificate,
    redacted_device_certificate,
    { q_device(table_alias="d", select="d.device_id", _id="device.device_certifier") } as device_certifier,
    created_on
FROM device
WHERE
    user_ = { q_user_internal_id(organization_id="$organization_id", user_id="$user_id") }
"""
)


_q_get_trustchain = Q(
    f"""
WITH RECURSIVE cte2 (
    _uid,
    _did,
    user_id,
    device_id,
    user_certifier,
    revoked_user_certifier,
    device_certifier,
    user_certificate,
    redacted_user_certificate,
    revoked_user_certificate,
    device_certificate,
    redacted_device_certificate
) AS (

    WITH cte (
        _uid,
        _did,
        user_id,
        device_id,
        user_certifier,
        revoked_user_certifier,
        device_certifier,
        user_certificate,
        redacted_user_certificate,
        revoked_user_certificate,
        device_certificate,
        redacted_device_certificate
    ) AS (
        SELECT
            user_._id AS _uid,
            device._id AS _did,
            user_id,
            device_id,
            user_certifier,
            revoked_user_certifier,
            device_certifier,
            user_certificate,
            redacted_user_certificate,
            revoked_user_certificate,
            device_certificate,
            redacted_device_certificate
        FROM device LEFT JOIN user_
        ON device.user_ = user_._id
        WHERE
            device.organization = { q_organization_internal_id("$organization_id") }
    )

    SELECT
        _uid,
        _did,
        user_id,
        device_id,
        user_certifier,
        revoked_user_certifier,
        device_certifier,
        user_certificate,
        redacted_user_certificate,
        revoked_user_certificate,
        device_certificate,
        redacted_device_certificate
    FROM cte
    WHERE
        _did = ANY({ q_device(organization_id="$organization_id", device_id="ANY($device_ids::VARCHAR[])", select="_id") })

    UNION

    SELECT
        cte._uid,
        cte._did,
        cte.user_id,
        cte.device_id,
        cte.user_certifier,
        cte.revoked_user_certifier,
        cte.device_certifier,
        cte.user_certificate,
        cte.redacted_user_certificate,
        cte.revoked_user_certificate,
        cte.device_certificate,
        cte.redacted_device_certificate
    FROM cte, cte2
    WHERE cte2.user_certifier = cte._did
        OR cte2.device_certifier = cte._did
        OR cte2.revoked_user_certifier = cte._did

)
SELECT DISTINCT ON (_did)
    _did,
    _uid,
    device_certificate,
    redacted_device_certificate,
    user_certificate,
    redacted_user_certificate,
    revoked_user_certificate
FROM cte2;
"""
)


async def _get_user(conn, organization_id: OrganizationID, user_id: UserID) -> User:
    row = await conn.fetchrow(*_q_get_user(organization_id=organization_id, user_id=user_id))
    if not row:
        raise UserNotFoundError(user_id)

    human_handle = None
    if row["human_email"]:
        human_handle = HumanHandle(email=row["human_email"], label=row["human_label"])

    return User(
        user_id=user_id,
        human_handle=human_handle,
        profile=STR_TO_USER_PROFILE[row["profile"]],
        user_certificate=row["user_certificate"],
        redacted_user_certificate=row["redacted_user_certificate"],
        user_certifier=row["user_certifier"],
        created_on=row["created_on"],
        revoked_on=row["revoked_on"],
        revoked_user_certificate=row["revoked_user_certificate"],
        revoked_user_certifier=row["revoked_user_certifier"],
    )


async def _get_device(conn, organization_id: OrganizationID, device_id: DeviceID) -> Device:
    row = await conn.fetchrow(*_q_get_device(organization_id=organization_id, device_id=device_id))
    if not row:
        raise UserNotFoundError(device_id)

    return Device(
        device_id=device_id,
        device_label=row["device_label"],
        device_certificate=row["device_certificate"],
        redacted_device_certificate=row["redacted_device_certificate"],
        device_certifier=row["device_certifier"],
        created_on=row["created_on"],
    )


async def _get_trustchain(
    conn, organization_id: OrganizationID, *device_ids: Optional[DeviceID], redacted: bool = False
) -> Trustchain:
    rows = await conn.fetch(
        *_q_get_trustchain(organization_id=organization_id, device_ids=device_ids)
    )
    user_certif_field = "redacted_user_certificate" if redacted else "user_certificate"
    device_certif_field = "redacted_device_certificate" if redacted else "device_certificate"

    users = {}
    revoked_users = {}
    devices = {}
    for row in rows:
        users[row["_uid"]] = row[user_certif_field]
        if row["revoked_user_certificate"] is not None:
            revoked_users[row["_uid"]] = row["revoked_user_certificate"]
        devices[row["_did"]] = row[device_certif_field]

    return Trustchain(
        users=tuple(users.values()),
        revoked_users=tuple(revoked_users.values()),
        devices=tuple(devices.values()),
    )


async def _get_user_devices(
    conn, organization_id: OrganizationID, user_id: UserID
) -> Tuple[Device, ...]:
    results = await conn.fetch(
        *_q_get_user_devices(organization_id=organization_id, user_id=user_id)
    )

    return tuple(
        Device(
            device_id=DeviceID(row["device_id"]),
            device_label=row["device_label"],
            device_certificate=row["device_certificate"],
            redacted_device_certificate=row["redacted_device_certificate"],
            device_certifier=row["device_certifier"],
            created_on=row["created_on"],
        )
        for row in results
    )


@query()
async def query_get_user(conn, organization_id: OrganizationID, user_id: UserID) -> User:
    return await _get_user(conn, organization_id, user_id)


@query(in_transaction=True)
async def query_get_user_with_trustchain(
    conn, organization_id: OrganizationID, user_id: UserID
) -> Tuple[User, Trustchain]:
    user = await _get_user(conn, organization_id, user_id)
    trustchain = await _get_trustchain(conn, organization_id, user.user_certifier)
    return user, trustchain


@query(in_transaction=True)
async def query_get_user_with_device_and_trustchain(
    conn, organization_id: OrganizationID, device_id: DeviceID
) -> Tuple[User, Device, Trustchain]:
    user = await _get_user(conn, organization_id, device_id.user_id)
    user_device = await _get_device(conn, organization_id, device_id)
    trustchain = await _get_trustchain(
        conn,
        organization_id,
        user.user_certifier,
        user.revoked_user_certifier,
        user_device.device_certifier,
    )
    return user, user_device, trustchain


@query(in_transaction=True)
async def query_get_user_with_devices_and_trustchain(
    conn, organization_id: OrganizationID, user_id: UserID, redacted: bool = False
) -> GetUserAndDevicesResult:
    user = await _get_user(conn, organization_id, user_id)
    user_devices = await _get_user_devices(conn, organization_id, user_id)
    trustchain = await _get_trustchain(
        conn,
        organization_id,
        user.user_certifier,
        user.revoked_user_certifier,
        *[device.device_certifier for device in user_devices],
        redacted=redacted,
    )
    return GetUserAndDevicesResult(
        user_certificate=user.redacted_user_certificate if redacted else user.user_certificate,
        revoked_user_certificate=user.revoked_user_certificate,
        device_certificates=tuple(
            d.redacted_device_certificate if redacted else d.device_certificate
            for d in user_devices
        ),
        trustchain_device_certificates=trustchain.devices,
        trustchain_user_certificates=trustchain.users,
        trustchain_revoked_user_certificates=trustchain.revoked_users,
    )


@query(in_transaction=True)
async def query_get_user_with_device(
    conn, organization_id: OrganizationID, device_id: DeviceID
) -> Tuple[User, Device]:
    d_row = await conn.fetchrow(
        *_q_get_device(organization_id=organization_id, device_id=device_id)
    )
    u_row = await conn.fetchrow(
        *_q_get_user(organization_id=organization_id, user_id=device_id.user_id)
    )
    if not u_row or not d_row:
        raise UserNotFoundError(device_id)

    human_handle = None
    if u_row["human_email"]:
        human_handle = HumanHandle(email=u_row["human_email"], label=u_row["human_label"])

    device = Device(
        device_id=device_id,
        device_label=d_row["device_label"],
        device_certificate=d_row["device_certificate"],
        redacted_device_certificate=d_row["redacted_device_certificate"],
        device_certifier=d_row["device_certifier"],
        created_on=d_row["created_on"],
    )
    user = User(
        user_id=device_id.user_id,
        human_handle=human_handle,
        profile=STR_TO_USER_PROFILE[u_row["profile"]],
        user_certificate=u_row["user_certificate"],
        redacted_user_certificate=u_row["redacted_user_certificate"],
        user_certifier=u_row["user_certifier"],
        created_on=u_row["created_on"],
        revoked_on=u_row["revoked_on"],
        revoked_user_certificate=u_row["revoked_user_certificate"],
        revoked_user_certifier=u_row["revoked_user_certifier"],
    )
    return user, device
