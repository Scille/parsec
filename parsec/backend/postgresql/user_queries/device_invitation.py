# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pypika import Parameter

from parsec.api.protocol import DeviceID, OrganizationID, UserID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.tables import (
    q_device,
    q_device_internal_id,
    q_device_invitation,
    q_organization_internal_id,
)
from parsec.backend.postgresql.utils import query
from parsec.backend.user import (
    DeviceInvitation,
    UserAlreadyExistsError,
    UserError,
    UserNotFoundError,
)

_q_device_exists = (
    q_device(organization_id=Parameter("$1"), device_id=Parameter("$2")).select(True).get_sql()
)


async def _device_exists(conn, organization_id: OrganizationID, device_id: DeviceID):
    device_result = await conn.fetchrow(_q_device_exists, organization_id, device_id)
    return bool(device_result)


_q_insert_invitation = """
INSERT INTO device_invitation (
    organization,
    creator,
    device_id,
    created_on
)
VALUES (
    ({}),
    ({}),
    ({}),
    ({})
)
ON CONFLICT (organization, device_id)
DO UPDATE
SET
    organization = excluded.organization,
    creator = excluded.creator,
    created_on = excluded.created_on
""".format(
    q_organization_internal_id(organization_id=Parameter("$1")),
    q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$2")),
    Parameter("$3"),
    Parameter("$4"),
)


_q_get_invitation = (
    q_device_invitation(organization_id=Parameter("$1"), device_id=Parameter("$2"))
    .select("device_id", q_device(_id=Parameter("creator")).select("device_id"), "created_on")
    .get_sql()
)


_q_delete_invitation = (
    q_device_invitation(organization_id=Parameter("$1"), device_id=Parameter("$2"))
    .delete()
    .get_sql()
)


async def _create_device_invitation(
    conn, organization_id: OrganizationID, invitation: DeviceInvitation
) -> None:
    if await _device_exists(conn, organization_id, invitation.device_id):
        raise UserAlreadyExistsError(f"Device `{invitation.device_id}` already exists")

    result = await conn.execute(
        _q_insert_invitation,
        organization_id,
        invitation.creator,
        invitation.device_id,
        invitation.created_on,
    )

    if result not in ("INSERT 0 1", "UPDATE 1"):
        raise UserError(f"Insertion error: {result}")


async def _get_device_invitation(
    conn, organization_id: OrganizationID, device_id: DeviceID
) -> DeviceInvitation:
    if await _device_exists(conn, organization_id, device_id):
        raise UserAlreadyExistsError(f"Device `{device_id}` already exists")

    result = await conn.fetchrow(_q_get_invitation, organization_id, device_id)
    if not result:
        raise UserNotFoundError(device_id)

    return DeviceInvitation(
        device_id=DeviceID(result[0]), creator=DeviceID(result[1]), created_on=result[2]
    )


@query(in_transaction=True)
async def query_create_device_invitation(
    conn, organization_id: OrganizationID, invitation: DeviceInvitation
) -> None:
    return await _create_device_invitation(conn, organization_id, invitation)


@query(in_transaction=True)
async def query_get_device_invitation(
    conn, organization_id: OrganizationID, device_id: UserID
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

    result = await conn.execute(_q_delete_invitation, organization_id, device_id)
    if result not in ("DELETE 1", "DELETE 0"):
        raise UserError(f"Deletion error: {result}")

    await send_signal(
        conn,
        BackendEvent.DEVICE_INVITATION_CANCELLED,
        organization_id=organization_id,
        device_id=device_id,
    )
