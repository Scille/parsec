# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from typing import Optional

import pendulum

from parsec.api.protocol import OrganizationID, UserID, DeviceID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import UserError, UserNotFoundError, UserAlreadyRevokedError
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_organization_internal_id,
    q_device_internal_id,
    q_user,
)


_q_revoke_user = Q(
    f"""
UPDATE user_ SET
    revoked_user_certificate = $revoked_user_certificate,
    revoked_user_certifier = { q_device_internal_id(organization_id="$organization_id", device_id="$revoked_user_certifier") },
    revoked_on = $revoked_on
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND user_id = $user_id
    AND revoked_on IS NULL
"""
)


_q_revoke_user_error = Q(
    q_user(organization_id="$organization_id", user_id="$user_id", select="revoked_on")
)


@query(in_transaction=True)
async def query_revoke_user(
    conn,
    organization_id: OrganizationID,
    user_id: UserID,
    revoked_user_certificate: bytes,
    revoked_user_certifier: DeviceID,
    revoked_on: Optional[pendulum.DateTime] = None,
) -> None:
    result = await conn.execute(
        *_q_revoke_user(
            organization_id=organization_id,
            user_id=user_id,
            revoked_user_certificate=revoked_user_certificate,
            revoked_user_certifier=revoked_user_certifier,
            revoked_on=revoked_on or pendulum.now(),
        )
    )

    if result != "UPDATE 1":
        # TODO: avoid having to do another query to find the error
        err_result = await conn.fetchrow(
            *_q_revoke_user_error(organization_id=organization_id, user_id=user_id)
        )
        if not err_result:
            raise UserNotFoundError(user_id)

        elif err_result[0]:
            raise UserAlreadyRevokedError()

        else:
            raise UserError(f"Update error: {result}")
    else:
        await send_signal(
            conn, BackendEvent.USER_REVOKED, organization_id=organization_id, user_id=user_id
        )
