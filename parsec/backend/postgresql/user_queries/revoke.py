# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
from pypika import Parameter

from parsec.api.protocol import DeviceID, OrganizationID, UserID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.tables import (
    q_device_internal_id,
    q_organization_internal_id,
    q_user,
)
from parsec.backend.postgresql.utils import query
from parsec.backend.user import UserAlreadyRevokedError, UserError, UserNotFoundError

_q_revoke_user = """
UPDATE user_ SET
    revoked_user_certificate = $3,
    revoked_user_certifier = ({}),
    revoked_on = $5
WHERE
    organization = ({})
    AND user_id = $2
    AND revoked_on IS NULL
""".format(
    q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$4")),
    q_organization_internal_id(Parameter("$1")),
)


_q_revoke_user_error = (
    q_user(organization_id=Parameter("$1"), user_id=Parameter("$2")).select("revoked_on").get_sql()
)


@query(in_transaction=True)
async def query_revoke_user(
    conn,
    organization_id: OrganizationID,
    user_id: UserID,
    revoked_user_certificate: bytes,
    revoked_user_certifier: DeviceID,
    revoked_on: pendulum.Pendulum = None,
) -> None:
    result = await conn.execute(
        _q_revoke_user,
        organization_id,
        user_id,
        revoked_user_certificate,
        revoked_user_certifier,
        revoked_on or pendulum.now(),
    )

    if result != "UPDATE 1":
        # TODO: avoid having to do another query to find the error
        err_result = await conn.fetchrow(_q_revoke_user_error, organization_id, user_id)
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
