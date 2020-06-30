# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import itertools
from typing import Optional
from uuid import UUID

from pendulum import now as pendulum_now
from pypika import Parameter
from triopg import UniqueViolationError

from parsec.api.protocol import OrganizationID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.postgresql.handler import send_signal
from parsec.backend.postgresql.tables import (
    q_device_internal_id,
    q_human_internal_id,
    q_organization_internal_id,
    q_user_internal_id,
    t_device,
    t_human,
    t_user,
)
from parsec.backend.postgresql.utils import Query, query
from parsec.backend.user import Device, User, UserAlreadyExistsError, UserError, UserNotFoundError

_q_get_user_devices = (
    Query.from_(t_device)
    .select(t_device.device_id)
    .where(
        t_device.user_
        == q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$2"))
    )
    .get_sql()
)


_q_get_not_revoked_users_for_human = (
    Query.from_(t_user)
    .select(t_user.user_id)
    .where(
        (
            t_user.human
            == q_human_internal_id(organization_id=Parameter("$1"), email=Parameter("$2"))
        )
        & (t_user.revoked_on.isnull() | t_user.revoked_on > Parameter("$3"))
    )
    .get_sql()
)


_q_insert_human_if_not_exists = (
    Query.into(t_human)
    .columns("organization", "email", "label")
    .insert(q_organization_internal_id(Parameter("$1")), Parameter("$2"), Parameter("$3"))
    .on_conflict()
    .do_nothing()
    .get_sql()
)


_q_insert_user = (
    Query.into(t_user)
    .columns(
        "organization",
        "user_id",
        "profile",
        "user_certificate",
        "redacted_user_certificate",
        "user_certifier",
        "created_on",
    )
    .insert(
        q_organization_internal_id(Parameter("$1")),
        Parameter("$2"),
        Parameter("$3"),
        Parameter("$4"),
        Parameter("$5"),
        q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$6")),
        Parameter("$7"),
    )
    .get_sql()
)


_q_insert_user_with_human_handle = (
    Query.into(t_user)
    .columns(
        "organization",
        "user_id",
        "profile",
        "user_certificate",
        "redacted_user_certificate",
        "user_certifier",
        "created_on",
        "human",
    )
    .insert(
        q_organization_internal_id(Parameter("$1")),
        Parameter("$2"),
        Parameter("$3"),
        Parameter("$4"),
        Parameter("$5"),
        q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$6")),
        Parameter("$7"),
        q_human_internal_id(organization_id=Parameter("$1"), email=Parameter("$8")),
    )
    .get_sql()
)


_q_insert_device = (
    Query.into(t_device)
    .columns(
        "organization",
        "user_",
        "device_id",
        "device_label",
        "device_certificate",
        "redacted_device_certificate",
        "device_certifier",
        "created_on",
    )
    .insert(
        q_organization_internal_id(Parameter("$1")),
        q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$2")),
        Parameter("$3"),
        Parameter("$4"),
        Parameter("$5"),
        Parameter("$6"),
        q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$7")),
        Parameter("$8"),
    )
    .get_sql()
)


async def _do_create_user_with_human_handle(
    conn, organization_id: OrganizationID, user: User, first_device: Device
) -> None:
    # Create human handle if needed
    await conn.execute(
        _q_insert_human_if_not_exists,
        organization_id,
        user.human_handle.email,
        user.human_handle.label,
    )

    # Now insert the new user
    try:
        result = await conn.execute(
            _q_insert_user_with_human_handle,
            organization_id,
            user.user_id,
            user.profile.value,
            user.user_certificate,
            user.redacted_user_certificate,
            user.user_certifier,
            user.created_on,
            user.human_handle.email,
        )

    except UniqueViolationError:
        raise UserAlreadyExistsError(f"User `{user.user_id}` already exists")

    if result != "INSERT 0 1":
        raise UserError(f"Insertion error: {result}")

    # Finally make sure there is only one non-revoked user with this human handle
    now = pendulum_now()
    not_revoked_users = await conn.fetch(
        _q_get_not_revoked_users_for_human, organization_id, user.human_handle.email, now
    )
    if len(not_revoked_users) != 1 or not_revoked_users[0]["user_id"] != user.user_id:
        # Exception cancels the transaction so the user insertion is automatically cancelled
        raise UserAlreadyExistsError(
            f"Human handle `{user.human_handle}` already corresponds to a non-revoked user"
        )


async def _do_create_user_without_human_handle(
    conn, organization_id: OrganizationID, user: User, first_device: Device
) -> None:
    try:
        result = await conn.execute(
            _q_insert_user,
            organization_id,
            user.user_id,
            user.profile.value,
            user.user_certificate,
            user.redacted_user_certificate,
            user.user_certifier,
            user.created_on,
        )

    except UniqueViolationError:
        raise UserAlreadyExistsError(f"User `{user.user_id}` already exists")

    if result != "INSERT 0 1":
        raise UserError(f"Insertion error: {result}")


async def _create_user(
    conn, organization_id: OrganizationID, user: User, first_device: Device
) -> None:
    if user.human_handle:
        await _do_create_user_with_human_handle(conn, organization_id, user, first_device)
    else:
        await _do_create_user_without_human_handle(conn, organization_id, user, first_device)

    await _create_device(conn, organization_id, first_device, first_device=True)

    # TODO: should be no longer needed once APIv1 is removed
    await send_signal(
        conn,
        BackendEvent.USER_CREATED,
        organization_id=organization_id,
        user_id=user.user_id,
        user_certificate=user.user_certificate,
        first_device_id=first_device.device_id,
        first_device_certificate=first_device.device_certificate,
    )


@query(in_transaction=True)
async def query_create_user(
    conn,
    organization_id: OrganizationID,
    user: User,
    first_device: Device,
    invitation_token: Optional[UUID] = None,
) -> None:
    await _create_user(conn, organization_id, user, first_device)


async def _create_device(
    conn, organization_id: OrganizationID, device: Device, first_device: bool = False
) -> None:
    if not first_device:
        existing_devices = await conn.fetch(_q_get_user_devices, organization_id, device.user_id)
        if not existing_devices:
            raise UserNotFoundError(f"User `{device.user_id}` doesn't exists")

        if device.device_id in itertools.chain(*existing_devices):
            raise UserAlreadyExistsError(f"Device `{device.device_id}` already exists")

    try:
        result = await conn.execute(
            _q_insert_device,
            organization_id,
            device.user_id,
            device.device_id,
            device.device_label,
            device.device_certificate,
            device.redacted_device_certificate,
            device.device_certifier,
            device.created_on,
        )
    except UniqueViolationError:
        raise UserAlreadyExistsError(f"Device `{device.device_id}` already exists")

    if result != "INSERT 0 1":
        raise UserError(f"Insertion error: {result}")


@query(in_transaction=True)
async def query_create_device(
    conn, organization_id: OrganizationID, device: Device, encrypted_answer: bytes = b""
) -> None:
    await _create_device(conn, organization_id, device, encrypted_answer)
    await send_signal(
        conn,
        BackendEvent.DEVICE_CREATED,
        organization_id=organization_id,
        device_id=device.device_id,
        device_certificate=device.device_certificate,
        encrypted_answer=encrypted_answer,
    )
