# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from typing import Optional, Tuple

from parsec.backend.backend_events import BackendEvent
from parsec.api.data import UserProfile
from parsec.api.protocol import OrganizationID, RealmRole
from parsec.backend.realm import (
    RealmGrantedRole,
    RealmAccessError,
    RealmIncompatibleProfileError,
    RealmRoleAlreadyGranted,
    RealmNotFoundError,
    RealmInMaintenanceError,
)
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.message import send_message
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_user,
    q_user_internal_id,
    q_device_internal_id,
    q_realm,
    q_realm_internal_id,
    STR_TO_REALM_ROLE,
)


_q_get_user_profile = Q(
    q_user(organization_id="$organization_id", user_id="$user_id", select="profile")
)


_q_get_realm_status = Q(
    q_realm(
        organization_id="$organization_id",
        realm_id="$realm_id",
        select="encryption_revision, maintenance_started_by, maintenance_started_on, maintenance_type",
    )
)


_q_get_roles = Q(
    f"""
SELECT
    { q_user_internal_id(organization_id="$organization_id", user_id="needle_user_id") },
    (
        SELECT role
        FROM realm_user_role
        WHERE
            user_ = { q_user_internal_id(organization_id="$organization_id", user_id="needle_user_id") }
            AND realm = { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") }
        ORDER BY certified_on DESC LIMIT 1
    )
FROM UNNEST($users_ids::VARCHAR[]) AS needle_user_id
"""
)


_q_insert_realm_user_role = Q(
    f"""
INSERT INTO realm_user_role(
    realm,
    user_,
    role,
    certificate,
    certified_by,
    certified_on
) SELECT
    { q_realm_internal_id(organization_id="$organization_id", realm_id="$realm_id") },
    { q_user_internal_id(organization_id="$organization_id", user_id="$user_id") },
    $role,
    $certificate,
    { q_device_internal_id(organization_id="$organization_id", device_id="$granted_by") },
    $granted_on
"""
)


@query(in_transaction=True)
async def query_update_roles(
    conn,
    organization_id: OrganizationID,
    new_role: RealmGrantedRole,
    recipient_message: Optional[bytes],
) -> None:
    assert new_role.granted_by is not None
    if new_role.granted_by.user_id == new_role.user_id:
        raise RealmAccessError("Cannot modify our own role")

    # Make sure user profile is compatible
    rep = await conn.fetchrow(
        *_q_get_user_profile(organization_id=organization_id, user_id=new_role.user_id)
    )
    if not rep:
        raise RealmNotFoundError(f"User `{new_role.user_id}` doesn't exist")
    if rep["profile"] == UserProfile.OUTSIDER.value and new_role.role in (
        RealmRole.MANAGER,
        RealmRole.OWNER,
    ):
        raise RealmIncompatibleProfileError("User with OUTSIDER profile cannot be MANAGER or OWNER")

    # Retrieve realm and make sure it is not under maintenance
    rep = await conn.fetchrow(
        *_q_get_realm_status(organization_id=organization_id, realm_id=new_role.realm_id)
    )
    if not rep:
        raise RealmNotFoundError(f"Realm `{new_role.realm_id}` doesn't exist")
    if rep["maintenance_type"]:
        raise RealmInMaintenanceError("Data realm is currently under maintenance")

    # Check access rights and user existance
    ((author_id, author_role), (user_id, existing_user_role)) = await conn.fetch(
        *_q_get_roles(
            organization_id=organization_id,
            realm_id=new_role.realm_id,
            users_ids=(new_role.granted_by.user_id, new_role.user_id),
        )
    )
    assert author_id
    assert user_id

    author_role = STR_TO_REALM_ROLE.get(author_role)
    existing_user_role = STR_TO_REALM_ROLE.get(existing_user_role)
    owner_only = (RealmRole.OWNER,)
    owner_or_manager = (RealmRole.OWNER, RealmRole.MANAGER)

    needed_roles: Tuple[RealmRole, ...]
    if existing_user_role in owner_or_manager or new_role.role in owner_or_manager:
        needed_roles = owner_only
    else:
        needed_roles = owner_or_manager

    if author_role not in needed_roles:
        raise RealmAccessError()

    if existing_user_role == new_role.role:
        raise RealmRoleAlreadyGranted()

    await conn.execute(
        *_q_insert_realm_user_role(
            organization_id=organization_id,
            realm_id=new_role.realm_id,
            user_id=new_role.user_id,
            role=new_role.role.value if new_role.role else None,
            certificate=new_role.certificate,
            granted_by=new_role.granted_by,
            granted_on=new_role.granted_on,
        )
    )

    await send_signal(
        conn,
        BackendEvent.REALM_ROLES_UPDATED,
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
