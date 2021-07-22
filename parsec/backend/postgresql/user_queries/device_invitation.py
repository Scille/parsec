# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from parsec.api.protocol import OrganizationID, DeviceID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import (
    UserError,
    UserNotFoundError,
    UserAlreadyExistsError,
    DeviceInvitation,
)
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_organization_internal_id,
    q_device,
    q_device_internal_id,
)


_q_device_exists = Q(
    f"""
SELECT true
FROM device
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND device_id = $device_id
"""
)


async def _device_exists(conn, organization_id: OrganizationID, device_id: DeviceID):
    device_result = await conn.fetchrow(
        *_q_device_exists(organization_id=organization_id, device_id=device_id)
    )
    return bool(device_result)


_q_insert_invitation = Q(
    f"""
INSERT INTO device_invitation (
    organization,
    creator,
    device_id,
    created_on
)
VALUES (
    { q_organization_internal_id("$organization_id") },
    { q_device_internal_id(organization_id="$organization_id", device_id="$creator") },
    $device_id,
    $created_on
)
ON CONFLICT (organization, device_id)
DO UPDATE
SET
    organization = excluded.organization,
    creator = excluded.creator,
    created_on = excluded.created_on
"""
)


_q_get_invitation = Q(
    f"""
SELECT
    { q_device(_id="device_invitation.creator", select="device_id") } as creator,
    created_on
FROM device_invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND device_id = $device_id
"""
)


_q_delete_invitation = Q(
    f"""
DELETE FROM device_invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND device_id = $device_id
"""
)


async def _create_device_invitation(
    conn, organization_id: OrganizationID, invitation: DeviceInvitation
) -> None:
    if await _device_exists(conn, organization_id, invitation.device_id):
        raise UserAlreadyExistsError(f"Device `{invitation.device_id}` already exists")

    result = await conn.execute(
        *_q_insert_invitation(
            organization_id=organization_id,
            creator=invitation.creator,
            device_id=invitation.device_id,
            created_on=invitation.created_on,
        )
    )

    if result not in ("INSERT 0 1", "UPDATE 1"):
        raise UserError(f"Insertion error: {result}")


async def _get_device_invitation(
    conn, organization_id: OrganizationID, device_id: DeviceID
) -> DeviceInvitation:
    if await _device_exists(conn, organization_id, device_id):
        raise UserAlreadyExistsError(f"Device `{device_id}` already exists")

    result = await conn.fetchrow(
        *_q_get_invitation(organization_id=organization_id, device_id=device_id)
    )
    if not result:
        raise UserNotFoundError(device_id)

    return DeviceInvitation(
        device_id=device_id, creator=DeviceID(result["creator"]), created_on=result["created_on"]
    )


@query(in_transaction=True)
async def query_create_device_invitation(
    conn, organization_id: OrganizationID, invitation: DeviceInvitation
) -> None:
    return await _create_device_invitation(conn, organization_id, invitation)


@query(in_transaction=True)
async def query_get_device_invitation(
    conn, organization_id: OrganizationID, device_id: DeviceID
) -> DeviceInvitation:
    return await _get_device_invitation(conn, organization_id, device_id)


@query(in_transaction=True)
async def query_claim_device_invitation(
    conn, organization_id: OrganizationID, device_id: DeviceID, encrypted_claim: bytes = b""
) -> DeviceInvitation:
    invitation = await _get_device_invitation(conn, organization_id, device_id)
    await send_signal(
        conn,
        BackendEvent.DEVICE_CLAIMED,
        organization_id=organization_id,
        device_id=invitation.device_id,
        encrypted_claim=encrypted_claim,
    )
    return invitation


@query(in_transaction=True)
async def query_cancel_device_invitation(
    conn, organization_id: OrganizationID, device_id: DeviceID
) -> None:
    if await _device_exists(conn, organization_id, device_id):
        raise UserAlreadyExistsError(f"Device `{device_id}` already exists")

    result = await conn.execute(
        *_q_delete_invitation(organization_id=organization_id, device_id=device_id)
    )
    if result not in ("DELETE 1", "DELETE 0"):
        raise UserError(f"Deletion error: {result}")

    await send_signal(
        conn,
        BackendEvent.DEVICE_INVITATION_CANCELLED,
        organization_id=organization_id,
        device_id=device_id,
    )
