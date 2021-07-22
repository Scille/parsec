# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from parsec.api.protocol import RealmRole, OrganizationID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.realm import RealmGrantedRole, RealmAlreadyExistsError
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.utils import (
    Q,
    query,
    q_organization_internal_id,
    q_user_internal_id,
    q_device_internal_id,
)


_q_insert_realm = Q(
    f"""
INSERT INTO realm (
    organization,
    realm_id,
    encryption_revision
) SELECT
    { q_organization_internal_id("$organization_id") },
    $realm_id,
    1
ON CONFLICT (organization, realm_id) DO NOTHING
RETURNING _id
"""
)


_q_insert_realm_role = Q(
    f"""
INSERT INTO realm_user_role(
    realm,
    user_,
    role,
    certificate,
    certified_by,
    certified_on
)
SELECT
    $realm_internal_id,
    { q_user_internal_id(organization_id="$organization_id", user_id="$user_id") },
    'OWNER',
    $certificate,
    { q_device_internal_id(organization_id="$organization_id", device_id="$certified_by") },
    $certified_on
"""
)


_q_insert_realm_encryption_revision = Q(
    """
INSERT INTO vlob_encryption_revision (
    realm,
    encryption_revision
)
SELECT
    $_id,
    1
"""
)


@query(in_transaction=True)
async def query_create(
    conn, organization_id: OrganizationID, self_granted_role: RealmGrantedRole
) -> None:
    assert self_granted_role.granted_by is not None
    assert self_granted_role.granted_by.user_id == self_granted_role.user_id
    assert self_granted_role.role == RealmRole.OWNER

    realm_internal_id = await conn.fetchval(
        *_q_insert_realm(organization_id=organization_id, realm_id=self_granted_role.realm_id)
    )
    if not realm_internal_id:
        raise RealmAlreadyExistsError()

    await conn.execute(
        *_q_insert_realm_role(
            realm_internal_id=realm_internal_id,
            organization_id=organization_id,
            user_id=self_granted_role.user_id,
            certificate=self_granted_role.certificate,
            certified_by=self_granted_role.granted_by,
            certified_on=self_granted_role.granted_on,
        )
    )

    await conn.execute(*_q_insert_realm_encryption_revision(_id=realm_internal_id))

    await send_signal(
        conn,
        BackendEvent.REALM_ROLES_UPDATED,
        organization_id=organization_id,
        author=self_granted_role.granted_by,
        realm_id=self_granted_role.realm_id,
        user=self_granted_role.user_id,
        role_str=self_granted_role.role.value,
    )
