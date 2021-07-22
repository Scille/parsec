# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import re
from functools import wraps
from parsec.api.data import UserProfile
from parsec.api.protocol import (
    RealmRole,
    MaintenanceType,
    InvitationType,
    InvitationStatus,
    InvitationDeletedReason,
)
from parsec.backend.invite import ConduitState
from parsec.backend.backend_events import BackendEvent


STR_TO_INVITATION_TYPE = {x.value: x for x in InvitationType}
STR_TO_INVITATION_STATUS = {x.value: x for x in InvitationStatus}
STR_TO_INVITATION_DELETED_REASON = {x.value: x for x in InvitationDeletedReason}
STR_TO_INVITATION_CONDUIT_STATE = {x.value: x for x in ConduitState}
STR_TO_BACKEND_EVENTS = {x.value: x for x in BackendEvent}
STR_TO_USER_PROFILE = {profile.value: profile for profile in UserProfile}
STR_TO_REALM_ROLE = {role.value: role for role in RealmRole}
STR_TO_REALM_MAINTENANCE_TYPE = {type.value: type for type in MaintenanceType}


class Q:
    """
    Dead simple SQL query composition framework (◠﹏◠)
    """

    def __init__(self, src, **kwargs):
        # retrieve variables
        variables = {}
        for candidate in re.findall(r"\$([a-zA-Z0-9_]+)", src):
            if candidate in variables:
                continue
            if candidate.isdigit():
                raise ValueError(f"Invalid variable name `{candidate}`")
            variables[candidate] = f"${len(variables) + 1}"
        self._variables = variables

        # Replace variables with their order-based equivalent
        # Variables are sorted in reverse to differentiate variables from others
        # with same name and a suffix, for example var and var_len
        for variable, order_based in sorted(variables.items(), reverse=True):
            src = src.replace(f"${variable}", order_based)

        self._sql = src
        self._stripped_sql = " ".join([x.strip() for x in src.split()])

    @property
    def sql(self):
        return self._sql

    def __call__(self, **kwargs):
        if kwargs.keys() != self._variables.keys():
            missing = self._variables.keys() - kwargs.keys()
            unknown = kwargs.keys() - self._variables.keys()
            raise ValueError(f"Invalid paramaters, missing: {missing}, unknown: {unknown}")
        args = [self._stripped_sql]
        for variable in self._variables:
            args.append(kwargs[variable])
        return args


def q_organization(organization_id=None, _id=None, table="organization", select="*"):
    assert organization_id is not None or _id is not None
    if _id is not None:
        condition = f"{table}._id = {_id}"
    else:
        condition = f"{table}.organization_id = {organization_id}"
    return f"(SELECT {select} FROM {table} WHERE {condition})"


def q_organization_internal_id(organization_id, **kwargs):
    return q_organization(organization_id=organization_id, select="_id", **kwargs)


def _table_q_factory(table, public_id_field):
    def _q(
        organization_id=None,
        organization=None,
        _id=None,
        table_alias=None,
        select="*",
        suffix=None,
        **kwargs,
    ):
        if table_alias:
            from_table = f"{table} as {table_alias}"
            select_table = table_alias
        else:
            from_table = table
            select_table = table
        if _id is not None:
            condition = f"{select_table}._id = {_id}"
        else:
            public_id = kwargs.pop(public_id_field, None)
            assert public_id is not None
            assert organization_id is not None or organization is not None
            if not organization:
                organization = q_organization_internal_id(organization_id)
            condition = f"{select_table}.organization = {organization} AND {select_table}.{ public_id_field } = { public_id }"
        assert not kwargs
        suffix = suffix or ""
        return f"(SELECT {select} FROM {from_table} WHERE {condition} {suffix})"

    def _q_internal_id(**kwargs):
        return _q(select="_id", **kwargs)

    _q.__name__ = f"q_{table}"
    _q_internal_id.__name__ = f"q_{table}_internal_id"

    return _q, _q_internal_id


q_device, q_device_internal_id = _table_q_factory("device", "device_id")
q_user, q_user_internal_id = _table_q_factory("user_", "user_id")
q_realm, q_realm_internal_id = _table_q_factory("realm", "realm_id")
q_block, q_block_internal_id = _table_q_factory("block", "block_id")
q_human, q_human_internal_id = _table_q_factory("human", "email")


def q_vlob_encryption_revision_internal_id(
    encryption_revision,
    organization_id=None,
    organization=None,
    realm_id=None,
    realm=None,
    table="vlob_encryption_revision",
):
    if realm is None:
        assert realm_id is not None
        assert organization_id is not None or organization is not None
        if organization is None:
            _q_realm = q_realm_internal_id(organization_id=organization_id, realm_id=realm_id)
        else:
            _q_realm = q_realm_internal_id(organization=organization, realm_id=realm_id)
    else:
        _q_realm = realm
    return f"""
(
SELECT _id
FROM {table}
WHERE
    {table}.realm = {_q_realm}
    AND {table}.encryption_revision = {encryption_revision}
)
"""


def q_user_can_read_vlob(
    user=None,
    user_id=None,
    realm=None,
    realm_id=None,
    organization=None,
    organization_id=None,
    table="realm_user_role",
):
    if user is None:
        assert organization_id is not None and user_id is not None
        _q_user = q_user_internal_id(
            organization=organization, organization_id=organization_id, user_id=user_id
        )
    else:
        _q_user = user

    if realm is None:
        assert organization_id is not None and realm_id is not None
        _q_realm = q_realm_internal_id(
            organization=organization, organization_id=organization_id, realm_id=realm_id
        )
    else:
        _q_realm = realm

    return f"""
COALESCE(
    (
        SELECT { table }.role IS NOT NULL
        FROM { table }
        WHERE
            { table }.realm = { _q_realm }
            AND { table }.user_ = { _q_user }
        ORDER BY certified_on DESC
        LIMIT 1
    ),
    False
)
"""


def q_user_can_write_vlob(
    user=None,
    user_id=None,
    realm=None,
    realm_id=None,
    organization=None,
    organization_id=None,
    table="realm_user_role",
):
    if user is None:
        assert organization_id is not None and user_id is not None
        _q_user = q_user_internal_id(
            organization=organization, organization_id=organization_id, user_id=user_id
        )
    else:
        _q_user = user

    if realm is None:
        assert organization_id is not None and realm_id is not None
        _q_realm = q_realm_internal_id(
            organization=organization, organization_id=organization_id, realm_id=realm_id
        )
    else:
        _q_realm = realm

    return f"""
COALESCE(
    (
        SELECT { table }.role IN ('CONTRIBUTOR', 'MANAGER', 'OWNER')
        FROM { table }
        WHERE
            { table }.realm = { _q_realm }
            AND { table }.user_ = { _q_user }
        ORDER BY certified_on DESC
        LIMIT 1
    ),
    False
)
"""


def query(in_transaction=False):
    if in_transaction:

        def decorator(fn):
            @wraps(fn)
            async def wrapper(conn, *args, **kwargs):
                async with conn.transaction():
                    return await fn(conn, *args, **kwargs)

            return wrapper

    else:

        def decorator(fn):
            return fn

    return decorator
