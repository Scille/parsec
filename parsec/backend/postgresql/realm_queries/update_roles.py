# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional
from pypika import Parameter
from pypika.enums import Order
from pendulum import now as pendulum_now

from parsec.api.protocol import RealmRole
from parsec.api.protocol import OrganizationID
from parsec.backend.realm import (
    RealmGrantedRole,
    RealmAccessError,
    RealmRoleAlreadyGranted,
    RealmNotFoundError,
    RealmInMaintenanceError,
)
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.utils import query
from parsec.backend.postgresql.message import send_message
from parsec.backend.postgresql.tables import (
    STR_TO_REALM_ROLE,
    q_realm_internal_id,
    q_realm,
    q_realm_user_role,
    q_user_internal_id,
    q_user,
    q_device_internal_id,
)


_q_get_realm_status = (
    q_realm(organization_id=Parameter("$1"), realm_id=Parameter("$2")).select(
        "encryption_revision",
        "maintenance_started_by",
        "maintenance_started_on",
        "maintenance_type",
    )
).get_sql()


_q_get_roles = """
SELECT
({}),
({}),
({})
FROM UNNEST($3::VARCHAR[]) AS needle_user_id
""".format(
    q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("needle_user_id")),
    q_realm_user_role(
        organization_id=Parameter("$1"),
        user_id=Parameter("needle_user_id"),
        realm_id=Parameter("$2"),
    )
    .select("role")
    .orderby("certified_on", order=Order.desc)
    .limit(1),
    q_user(organization_id=Parameter("$1"), user_id=Parameter("needle_user_id")).select(
        "revoked_on"
    ),
)


_q_insert_realm_user_role = """
INSERT INTO realm_user_role(
    realm,
    user_,
    role,
    certificate,
    certified_by,
    certified_on
) SELECT
    ({}),
    ({}),
    ({}),
    ({}),
    ({}),
    ({})
""".format(
    q_realm_internal_id(organization_id=Parameter("$1"), realm_id=Parameter("$2")),
    q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$3")),
    Parameter("$4"),
    Parameter("$5"),
    q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$6")),
    Parameter("$7"),
)


@query(in_transaction=True)
async def query_update_roles(
    conn,
    organization_id: OrganizationID,
    new_role: RealmGrantedRole,
    recipient_message: Optional[bytes],
) -> None:
    if new_role.granted_by.user_id == new_role.user_id:
        raise RealmAccessError("Cannot modify our own role")

    # Retrieve realm and make sure it is not under maintenance
    rep = await conn.fetchrow(_q_get_realm_status, organization_id, new_role.realm_id)
    if not rep:
        raise RealmNotFoundError(f"Realm `{new_role.realm_id}` doesn't exist")
    if rep["maintenance_type"]:
        raise RealmInMaintenanceError("Data realm is currently under maintenance")

    # Check access rights and user existance
    (
        (author_id, author_role, _),
        (user_id, existing_user_role, user_revoked_on),
    ) = await conn.fetch(
        _q_get_roles,
        organization_id,
        new_role.realm_id,
        (new_role.granted_by.user_id, new_role.user_id),
    )
    assert author_id
    if not user_id:
        raise RealmNotFoundError(f"User `{new_role.user_id}` doesn't exist")

    if user_revoked_on and user_revoked_on <= pendulum_now() and new_role.role is not None:
        raise RealmAccessError(
            f"User `{new_role.user_id}` has been revoked, can only remove it role"
        )

    author_role = STR_TO_REALM_ROLE.get(author_role)
    existing_user_role = STR_TO_REALM_ROLE.get(existing_user_role)
    owner_only = (RealmRole.OWNER,)
    owner_or_manager = (RealmRole.OWNER, RealmRole.MANAGER)
    if existing_user_role in owner_or_manager or new_role.role in owner_or_manager:
        needed_roles = owner_only
    else:
        needed_roles = owner_or_manager

    if author_role not in needed_roles:
        raise RealmAccessError()

    if existing_user_role == new_role.role:
        raise RealmRoleAlreadyGranted()

    await conn.execute(
        _q_insert_realm_user_role,
        organization_id,
        new_role.realm_id,
        new_role.user_id,
        new_role.role.value if new_role.role else None,
        new_role.certificate,
        new_role.granted_by,
        new_role.granted_on,
    )

    await send_signal(
        conn,
        "realm.roles_updated",
        organization_id=organization_id,
        author=new_role.granted_by,
        realm_id=new_role.realm_id,
        user=new_role.user_id,
        role_str=new_role.role.value if new_role.role else None,
    )

    if recipient_message:
        await send_message(
            conn,
            organization_id,
            new_role.granted_by,
            new_role.user_id,
            new_role.granted_on,
            recipient_message,
        )
