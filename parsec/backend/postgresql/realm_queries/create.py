# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pypika import Parameter
from pypika.terms import ValueWrapper

from parsec.api.protocol import OrganizationID, RealmRole
from parsec.backend.backend_events import BackendEvent
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.tables import (
    q_device_internal_id,
    q_organization_internal_id,
    q_user_internal_id,
)
from parsec.backend.postgresql.utils import query
from parsec.backend.realm import RealmAlreadyExistsError, RealmGrantedRole

_q_insert_realm = """
INSERT INTO realm (
    organization,
    realm_id,
    encryption_revision
) SELECT
    ({}),
    ({}),
    1
ON CONFLICT (organization, realm_id) DO NOTHING
RETURNING
_id
""".format(
    q_organization_internal_id(Parameter("$1")), Parameter("$2")
)


_q_insert_realm_role = """
INSERT INTO realm_user_role(
    realm,
    user_,
    role,
    certificate,
    certified_by,
    certified_on
)
SELECT
    ({}), ({}), ({}), ({}), ({}), ({})
""".format(
    Parameter("$1"),
    q_user_internal_id(organization_id=Parameter("$2"), user_id=Parameter("$3")),
    ValueWrapper("OWNER"),
    Parameter("$4"),
    q_device_internal_id(organization_id=Parameter("$2"), device_id=Parameter("$5")),
    Parameter("$6"),
)


_q_insert_realm_encryption_revision = """
INSERT INTO vlob_encryption_revision (
    realm,
    encryption_revision
)
SELECT
    $1,
    1
"""


@query(in_transaction=True)
async def query_create(
    conn, organization_id: OrganizationID, self_granted_role: RealmGrantedRole
) -> None:
    assert self_granted_role.granted_by.user_id == self_granted_role.user_id
    assert self_granted_role.role == RealmRole.OWNER

    realm_internal_id = await conn.fetchval(
        _q_insert_realm, organization_id, self_granted_role.realm_id
    )
    if not realm_internal_id:
        raise RealmAlreadyExistsError()

    await conn.execute(
        _q_insert_realm_role,
        realm_internal_id,
        organization_id,
        self_granted_role.user_id,
        self_granted_role.certificate,
        self_granted_role.granted_by,
        self_granted_role.granted_on,
    )

    await conn.execute(_q_insert_realm_encryption_revision, realm_internal_id)

    await send_signal(
        conn,
        BackendEvent.REALM_ROLES_UPDATED,
        organization_id=organization_id,
        author=self_granted_role.granted_by,
        realm_id=self_granted_role.realm_id,
        user=self_granted_role.user_id,
        role_str=self_granted_role.role.value,
    )
