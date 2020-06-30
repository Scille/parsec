# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pypika import Parameter

from parsec.api.protocol import DeviceID, OrganizationID, UserID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.tables import (
    q_device,
    q_device_internal_id,
    q_organization_internal_id,
    q_user,
    q_user_invitation,
)
from parsec.backend.postgresql.utils import query
from parsec.backend.user import UserAlreadyExistsError, UserError, UserInvitation, UserNotFoundError

_q_user_exists = (
    q_user(organization_id=Parameter("$1"), user_id=Parameter("$2")).select(True).get_sql()
)


async def _user_exists(conn, organization_id: OrganizationID, user_id: UserID):
    user_result = await conn.fetchrow(_q_user_exists, organization_id, user_id)
    return bool(user_result)


_q_insert_invitation = """
INSERT INTO user_invitation (
    organization,
    creator,
    user_id,
    created_on
) VALUES (
    ({}),
    ({}),
    ({}),
    ({})
)
ON CONFLICT (organization, user_id)
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
    q_user_invitation(organization_id=Parameter("$1"), user_id=Parameter("$2"))
    .select("user_id", q_device(_id=Parameter("creator")).select("device_id"), "created_on")
    .get_sql()
)


_q_delete_invitation = (
    q_user_invitation(organization_id=Parameter("$1"), user_id=Parameter("$2")).delete().get_sql()
)


async def _get_user_invitation(conn, organization_id: OrganizationID, user_id: UserID):
    if await _user_exists(conn, organization_id, user_id):
        raise UserAlreadyExistsError(f"User `{user_id}` already exists")

    result = await conn.fetchrow(_q_get_invitation, organization_id, user_id)
    if not result:
        raise UserNotFoundError(user_id)

    return UserInvitation(
        user_id=UserID(result[0]), creator=DeviceID(result[1]), created_on=result[2]
    )


@query(in_transaction=True)
async def query_create_user_invitation(
    conn, organization_id: OrganizationID, invitation: UserInvitation
) -> None:
    if await _user_exists(conn, organization_id, invitation.user_id):
        raise UserAlreadyExistsError(f"User `{invitation.user_id}` already exists")

    result = await conn.execute(
        _q_insert_invitation,
        organization_id,
        invitation.creator,
        invitation.user_id,
        invitation.created_on,
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

    result = await conn.execute(_q_delete_invitation, organization_id, user_id)
    if result not in ("DELETE 1", "DELETE 0"):
        raise UserError(f"Deletion error: {result}")
