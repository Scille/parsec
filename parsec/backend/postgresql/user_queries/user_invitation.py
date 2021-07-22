# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from parsec.api.protocol import OrganizationID, UserID, DeviceID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import UserError, UserNotFoundError, UserAlreadyExistsError, UserInvitation
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_organization_internal_id,
    q_device,
    q_device_internal_id,
    q_user,
)


_q_user_exists = Q(q_user(organization_id="$organization_id", user_id="$user_id", select="TRUE"))


async def _user_exists(conn, organization_id: OrganizationID, user_id: UserID):
    user_result = await conn.fetchrow(
        *_q_user_exists(organization_id=organization_id, user_id=user_id)
    )
    return bool(user_result)


_q_insert_invitation = Q(
    f"""
INSERT INTO user_invitation (
    organization,
    creator,
    user_id,
    created_on
) VALUES (
    { q_organization_internal_id("$organization_id") },
    { q_device_internal_id(organization_id="$organization_id", device_id="$creator")},
    $user_id,
    $created_on
)
ON CONFLICT (organization, user_id)
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
    { q_device(_id="creator", select="device_id") } as creator,
    created_on
FROM user_invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND user_id = $user_id
"""
)


_q_delete_invitation = Q(
    f"""
DELETE FROM user_invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND user_id = $user_id
"""
)


async def _get_user_invitation(conn, organization_id: OrganizationID, user_id: UserID):
    if await _user_exists(conn, organization_id, user_id):
        raise UserAlreadyExistsError(f"User `{user_id}` already exists")

    result = await conn.fetchrow(
        *_q_get_invitation(organization_id=organization_id, user_id=user_id)
    )
    if not result:
        raise UserNotFoundError(user_id)

    return UserInvitation(
        user_id=user_id, creator=DeviceID(result["creator"]), created_on=result["created_on"]
    )


@query(in_transaction=True)
async def query_create_user_invitation(
    conn, organization_id: OrganizationID, invitation: UserInvitation
) -> None:
    if await _user_exists(conn, organization_id, invitation.user_id):
        raise UserAlreadyExistsError(f"User `{invitation.user_id}` already exists")

    result = await conn.execute(
        *_q_insert_invitation(
            organization_id=organization_id,
            creator=invitation.creator,
            user_id=invitation.user_id,
            created_on=invitation.created_on,
        )
    )

    if result not in ("INSERT 0 1", "UPDATE 1"):
        raise UserError(f"Insertion error: {result}")


@query(in_transaction=True)
async def query_get_user_invitation(conn, organization_id: OrganizationID, user_id: UserID):
    return await _get_user_invitation(conn, organization_id, user_id)


@query(in_transaction=True)
async def query_claim_user_invitation(
    conn, organization_id: OrganizationID, user_id: UserID, encrypted_claim: bytes = b""
) -> UserInvitation:
    invitation = await _get_user_invitation(conn, organization_id, user_id)
    await send_signal(
        conn,
        BackendEvent.USER_CLAIMED,
        organization_id=organization_id,
        user_id=invitation.user_id,
        encrypted_claim=encrypted_claim,
    )
    return invitation


@query(in_transaction=True)
async def query_cancel_user_invitation(
    conn, organization_id: OrganizationID, user_id: UserID
) -> None:
    if await _user_exists(conn, organization_id, user_id):
        raise UserAlreadyExistsError(f"User `{user_id}` already exists")

    result = await conn.execute(
        *_q_delete_invitation(organization_id=organization_id, user_id=user_id)
    )
    if result not in ("DELETE 1", "DELETE 0"):
        raise UserError(f"Deletion error: {result}")

    await send_signal(
        conn,
        BackendEvent.USER_INVITATION_CANCELLED,
        organization_id=organization_id,
        user_id=user_id,
    )
