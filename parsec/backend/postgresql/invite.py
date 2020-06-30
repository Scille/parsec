# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import re
from typing import List, Optional
from uuid import UUID, uuid4

from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.protocol import (
    HumanHandle,
    InvitationDeletedReason,
    InvitationStatus,
    InvitationType,
    OrganizationID,
    UserID,
)
from parsec.backend.backend_events import BackendEvent
from parsec.backend.invite import (
    NEXT_CONDUIT_STATE,
    BaseInviteComponent,
    ConduitListenCtx,
    ConduitState,
    DeviceInvitation,
    Invitation,
    InvitationAlreadyDeletedError,
    InvitationInvalidStateError,
    InvitationNotFoundError,
    UserInvitation,
)
from parsec.backend.postgresql.handler import PGHandler, send_signal
from parsec.backend.postgresql.tables import STR_TO_INVITATION_CONDUIT_STATE


class Q:
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
        for variable, order_based in variables.items():
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


def q_user(
    organization_id=None, organization=None, user_id=None, _id=None, table="user_", select="*"
):
    if _id is not None:
        condition = f"{table}._id = {_id}"
    else:
        assert user_id is not None
        assert organization_id is not None or organization is not None
        if not organization:
            organization = q_organization_internal_id(organization_id)
        condition = f"{table}.organization = {organization} AND {table}.user_id = {user_id}"
    return f"(SELECT {select} FROM {table} WHERE {condition})"


def q_user_internal_id(user_id, organization_id=None, organization=None, **kwargs):
    assert (not organization_id and organization) or (organization_id and not organization)
    return q_user(
        user_id=user_id,
        organization_id=organization_id,
        organization=organization,
        select="_id",
        **kwargs,
    )


_q_retrieve_compatible_user_invitation = Q(
    f"""
SELECT
    token
FROM invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND type = $type
    AND greeter = { q_user_internal_id(organization_id="$organization_id", user_id="$greeter_user_id") }
    AND claimer_email = $claimer_email
    AND deleted_on IS NULL
LIMIT 1
"""
)


_q_retrieve_compatible_device_invitation = Q(
    f"""
SELECT
    token
FROM invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND type = $type
    AND greeter = { q_user_internal_id(organization_id="$organization_id", user_id="$greeter_user_id") }
    AND claimer_email IS NULL
    AND deleted_on IS NULL
LIMIT 1
"""
)


_q_insert_invitation = Q(
    f"""
INSERT INTO invitation(
    organization,
    token,
    type,
    greeter,
    claimer_email,
    created_on
)
VALUES (
    { q_organization_internal_id("$organization_id") },
    $token,
    $type,
    { q_user_internal_id(organization_id="$organization_id", user_id="$greeter_user_id") },
    $claimer_email,
    $created_on
)
"""
)


_q_delete_invitation_info = Q(
    f"""
SELECT
    _id,
    deleted_on
FROM invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND greeter = { q_user_internal_id(organization_id="$organization_id", user_id="$greeter")}
    AND token = $token
FOR UPDATE
"""
)


_q_delete_invitation = Q(
    f"""
UPDATE invitation
SET
    deleted_on = $on,
    deleted_reason = $reason
WHERE
    _id = $row_id
"""
)


async def _do_delete_invitation(
    conn,
    organization_id: OrganizationID,
    greeter: UserID,
    token: UUID,
    on: Pendulum,
    reason: InvitationDeletedReason,
):
    row = await conn.fetchrow(
        *_q_delete_invitation_info(organization_id=organization_id, greeter=greeter, token=token)
    )
    if not row:
        raise InvitationNotFoundError(token)
    row_id, deleted_on = row
    if deleted_on:
        raise InvitationAlreadyDeletedError(token)

    await conn.execute(*_q_delete_invitation(row_id=row_id, on=on, reason=reason.value))
    await send_signal(
        conn,
        BackendEvent.INVITE_STATUS_CHANGED,
        organization_id=organization_id,
        greeter=greeter,
        token=token,
        status_str=InvitationStatus.DELETED.value,
    )


_q_human_handle_per_user = f"""
SELECT user_._id AS user_, email, label
FROM human LEFT JOIN user_ ON human._id = user_.human
WHERE
    human.organization = { q_organization_internal_id("$organization_id") }
    AND user_.revoked_on IS NULL
"""

_q_list_invitations = Q(
    f"""
WITH human_handle_per_user AS ({_q_human_handle_per_user})
SELECT
    token,
    type,
    { q_user(_id="greeter", select="user_id") },
    human_handle_per_user.email,
    human_handle_per_user.label,
    claimer_email,
    created_on,
    deleted_on,
    deleted_reason
FROM invitation LEFT JOIN human_handle_per_user on invitation.greeter = human_handle_per_user.user_
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND greeter = { q_user_internal_id(organization_id="$organization_id", user_id="$greeter_user_id") }
    AND deleted_on IS NULL
ORDER BY created_on
"""
)


_q_info_invitation = Q(
    f"""
WITH human_handle_per_user AS ({_q_human_handle_per_user})
SELECT
    type,
    { q_user(_id="greeter", select="user_id") },
    human_handle_per_user.email,
    human_handle_per_user.label,
    claimer_email,
    created_on,
    deleted_on,
    deleted_reason
FROM invitation LEFT JOIN human_handle_per_user on invitation.greeter = human_handle_per_user.user_
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND token = $token
LIMIT 1
"""
)


_q_conduit_greeter_info = Q(
    f"""
SELECT
    _id,
    conduit_state,
    conduit_greeter_payload,
    conduit_claimer_payload,
    deleted_on
FROM invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND token = $token
    AND greeter = { q_user_internal_id(organization_id="$organization_id", user_id="$greeter_user_id") }
FOR UPDATE
"""
)


_q_conduit_claimer_info = Q(
    f"""
SELECT
    _id,
    conduit_state,
    conduit_greeter_payload,
    conduit_claimer_payload,
    deleted_on
FROM invitation
WHERE
    organization = { q_organization_internal_id("$organization_id") }
    AND token = $token
FOR UPDATE
"""
)


_q_conduit_update = Q(
    f"""
UPDATE invitation
SET
    conduit_state = $conduit_state,
    conduit_greeter_payload = $conduit_greeter_payload,
    conduit_claimer_payload = $conduit_claimer_payload
WHERE
    _id = $row_id
"""
)


async def _conduit_talk(
    conn,
    organization_id: OrganizationID,
    greeter: Optional[UserID],
    token: UUID,
    state: ConduitState,
    payload: bytes,
) -> ConduitListenCtx:
    is_greeter = greeter is not None
    async with conn.transaction():
        # On top of retrieving the invitation row, this query lock the row
        # in the database for the duration of the transaction.
        # Hence concurrent request will be on hold until the end of the transaction.

        if is_greeter:
            row = await conn.fetchrow(
                *_q_conduit_greeter_info(
                    organization_id=organization_id, greeter_user_id=greeter, token=token
                )
            )
        else:
            row = await conn.fetchrow(
                *_q_conduit_claimer_info(organization_id=organization_id, token=token)
            )

        if not row:
            raise InvitationNotFoundError(token)

        row_id = row["_id"]
        curr_conduit_state = STR_TO_INVITATION_CONDUIT_STATE[row["conduit_state"]]
        curr_greeter_payload = row["conduit_greeter_payload"]
        curr_claimer_payload = row["conduit_claimer_payload"]

        if is_greeter:
            curr_our_payload = curr_greeter_payload
            curr_peer_payload = curr_claimer_payload
        else:
            curr_our_payload = curr_claimer_payload
            curr_peer_payload = curr_greeter_payload

        if row["deleted_on"]:
            raise InvitationAlreadyDeletedError(token)

        if curr_conduit_state != state or curr_our_payload is not None:
            # We are out of sync with the conduit:
            # - the conduit state has changed in our back (maybe reseted by the peer)
            # - we want to reset the conduit
            # - we have already provided a payload for the current conduit state (most
            #   likely because a retry of a command that failed due to connection outage)
            if state == ConduitState.STATE_1_WAIT_PEERS:
                # We wait to reset the conduit
                curr_conduit_state = state
                curr_claimer_payload = None
                curr_greeter_payload = None
                curr_our_payload = None
                curr_peer_payload = None
            else:
                raise InvitationInvalidStateError()

        # Now update the conduit with our payload and send a signal if
        # the peer is already waiting for us.
        if is_greeter:
            curr_greeter_payload = payload
        else:
            curr_claimer_payload = payload
        await conn.execute(
            *_q_conduit_update(
                row_id=row_id,
                conduit_state=curr_conduit_state.value,
                conduit_greeter_payload=curr_greeter_payload,
                conduit_claimer_payload=curr_claimer_payload,
            )
        )
        # Note that in case of conduit reset, this signal will lure the peer into
        # thinking we have answered so he will wakeup and take into account the reset
        await send_signal(
            conn, BackendEvent.INVITE_CONDUIT_UPDATED, organization_id=organization_id, token=token
        )

    return ConduitListenCtx(
        organization_id=organization_id,
        greeter=greeter,
        token=token,
        state=state,
        payload=payload,
        peer_payload=curr_peer_payload,
    )


async def _conduit_listen(conn, ctx: ConduitListenCtx) -> Optional[bytes]:
    async with conn.transaction():
        if ctx.is_greeter:
            row = await conn.fetchrow(
                *_q_conduit_greeter_info(
                    organization_id=ctx.organization_id,
                    greeter_user_id=ctx.greeter,
                    token=ctx.token,
                )
            )
        else:
            row = await conn.fetchrow(
                *_q_conduit_claimer_info(organization_id=ctx.organization_id, token=ctx.token)
            )

        if not row:
            raise InvitationNotFoundError()

        row_id = row["_id"]
        curr_conduit_state = STR_TO_INVITATION_CONDUIT_STATE[row["conduit_state"]]

        if row["deleted_on"]:
            raise InvitationAlreadyDeletedError()

        if ctx.is_greeter:
            curr_our_payload = row["conduit_greeter_payload"]
            curr_peer_payload = row["conduit_claimer_payload"]
        else:
            curr_our_payload = row["conduit_claimer_payload"]
            curr_peer_payload = row["conduit_greeter_payload"]

        if ctx.peer_payload is None:
            # We are waiting for the peer to provite it payload

            # Only peer payload should be allowed to change
            if curr_conduit_state != ctx.state or curr_our_payload != ctx.payload:
                raise InvitationInvalidStateError()

            if curr_peer_payload is not None:
                # Our peer has provided it payload (hence it knows
                # about our payload too), we can update the conduit
                # to the next state
                await conn.execute(
                    *_q_conduit_update(
                        row_id=row_id,
                        conduit_state=NEXT_CONDUIT_STATE[ctx.state].value,
                        conduit_greeter_payload=None,
                        conduit_claimer_payload=None,
                    )
                )
                await send_signal(
                    conn,
                    BackendEvent.INVITE_CONDUIT_UPDATED,
                    organization_id=ctx.organization_id,
                    token=ctx.token,
                )
                return curr_peer_payload

        else:
            # We were waiting for the peer to take into account the
            # payload we provided. This would be done once the conduit
            # has switched to it next state.

            if curr_conduit_state == NEXT_CONDUIT_STATE[ctx.state] and curr_our_payload is None:
                return ctx.peer_payload
            elif (
                curr_conduit_state != ctx.state
                or curr_our_payload != ctx.payload
                or curr_peer_payload != ctx.peer_payload
            ):
                # Something unexpected has changed in our back...
                raise InvitationInvalidStateError()

    # Peer hasn't answered yet, we should wait and retry later...
    return None


async def _do_new_user_invitation(
    conn,
    organization_id: OrganizationID,
    greeter_user_id: UserID,
    claimer_email: Optional[str],
    created_on: Pendulum,
) -> UUID:
    if claimer_email:
        invitation_type = InvitationType.USER
        q = _q_retrieve_compatible_user_invitation(
            organization_id=organization_id,
            type=invitation_type.value,
            greeter_user_id=greeter_user_id,
            claimer_email=claimer_email,
        )
    else:
        invitation_type = InvitationType.DEVICE
        q = _q_retrieve_compatible_device_invitation(
            organization_id=organization_id,
            type=invitation_type.value,
            greeter_user_id=greeter_user_id,
        )

    # Check if no compatible invitations already exists
    row = await conn.fetchrow(*q)
    if row:
        token = row["token"]
    else:
        # No risk of UniqueViolationError given token is a uuid4
        token = uuid4()
        await conn.execute(
            *_q_insert_invitation(
                organization_id=organization_id,
                type=invitation_type.value,
                token=token,
                greeter_user_id=greeter_user_id,
                claimer_email=claimer_email,
                created_on=created_on,
            )
        )
    await send_signal(
        conn,
        BackendEvent.INVITE_STATUS_CHANGED,
        organization_id=organization_id,
        greeter=greeter_user_id,
        token=token,
        status_str=InvitationStatus.IDLE.value,
    )
    return token


class PGInviteComponent(BaseInviteComponent):
    def __init__(self, dbh: PGHandler, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dbh = dbh

    async def new_for_user(
        self,
        organization_id: OrganizationID,
        greeter_user_id: UserID,
        claimer_email: str,
        created_on: Optional[Pendulum] = None,
    ) -> UserInvitation:
        """
        Raise: Nothing
        """
        created_on = created_on or pendulum_now()
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            token = await _do_new_user_invitation(
                conn,
                organization_id=organization_id,
                greeter_user_id=greeter_user_id,
                claimer_email=claimer_email,
                created_on=created_on,
            )
        return UserInvitation(
            greeter_user_id=greeter_user_id,
            greeter_human_handle=None,
            claimer_email=claimer_email,
            token=token,
            created_on=created_on,
        )

    async def new_for_device(
        self,
        organization_id: OrganizationID,
        greeter_user_id: UserID,
        created_on: Optional[Pendulum] = None,
    ) -> DeviceInvitation:
        """
        Raise: Nothing
        """
        created_on = created_on or pendulum_now()
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            token = await _do_new_user_invitation(
                conn,
                organization_id=organization_id,
                greeter_user_id=greeter_user_id,
                claimer_email=None,
                created_on=created_on,
            )
        return DeviceInvitation(
            greeter_user_id=greeter_user_id,
            greeter_human_handle=None,
            token=token,
            created_on=created_on,
        )

    async def delete(
        self,
        organization_id: OrganizationID,
        greeter: UserID,
        token: UUID,
        on: Pendulum,
        reason: InvitationDeletedReason,
    ) -> None:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            await _do_delete_invitation(conn, organization_id, greeter, token, on, reason)

    async def list(self, organization_id: OrganizationID, greeter: UserID) -> List[Invitation]:
        async with self.dbh.pool.acquire() as conn:
            rows = await conn.fetch(
                *_q_list_invitations(organization_id=organization_id, greeter_user_id=greeter)
            )

        invitations_with_claimer_online = self._claimers_ready[organization_id]
        invitations = []
        for (
            token,
            type,
            greeter,
            greeter_human_handle_email,
            greeter_human_handle_label,
            claimer_email,
            created_on,
            deleted_on,
            deleted_reason,
        ) in rows:
            if greeter_human_handle_email:
                greeter_human_handle = HumanHandle(
                    email=greeter_human_handle_email, label=greeter_human_handle_label
                )
            else:
                greeter_human_handle = None

            if deleted_on:
                status = InvitationStatus.DELETED
            elif token in invitations_with_claimer_online:
                status = InvitationStatus.READY
            else:
                status = InvitationStatus.IDLE

            if type == InvitationType.USER.value:
                invitation = UserInvitation(
                    greeter_user_id=UserID(greeter),
                    greeter_human_handle=greeter_human_handle,
                    claimer_email=claimer_email,
                    token=token,
                    created_on=created_on,
                    status=status,
                )
            else:  # Device
                invitation = DeviceInvitation(
                    greeter_user_id=UserID(greeter),
                    greeter_human_handle=greeter_human_handle,
                    token=token,
                    created_on=created_on,
                    status=status,
                )
            invitations.append(invitation)
        return invitations

    async def info(self, organization_id: OrganizationID, token: UUID) -> Invitation:
        async with self.dbh.pool.acquire() as conn:
            row = await conn.fetchrow(
                *_q_info_invitation(organization_id=organization_id, token=token)
            )
        if not row:
            raise InvitationNotFoundError(token)

        (
            type,
            greeter,
            greeter_human_handle_email,
            greeter_human_handle_label,
            claimer_email,
            created_on,
            deleted_on,
            deleted_reason,
        ) = row

        if deleted_on:
            raise InvitationAlreadyDeletedError(token)

        if greeter_human_handle_email:
            greeter_human_handle = HumanHandle(
                email=greeter_human_handle_email, label=greeter_human_handle_label
            )
        else:
            greeter_human_handle = None
        if type == InvitationType.USER.value:
            return UserInvitation(
                greeter_user_id=UserID(greeter),
                greeter_human_handle=greeter_human_handle,
                claimer_email=claimer_email,
                token=token,
                created_on=created_on,
                status=InvitationStatus.READY,
            )
        else:  # Device
            return DeviceInvitation(
                greeter_user_id=UserID(greeter),
                greeter_human_handle=greeter_human_handle,
                token=token,
                created_on=created_on,
                status=InvitationStatus.READY,
            )

    async def _conduit_talk(
        self,
        organization_id: OrganizationID,
        greeter: UserID,
        token: UUID,
        state: ConduitState,
        payload: bytes,
    ) -> ConduitListenCtx:
        async with self.dbh.pool.acquire() as conn:
            return await _conduit_talk(conn, organization_id, greeter, token, state, payload)

    async def _conduit_listen(self, ctx: ConduitListenCtx) -> Optional[bytes]:
        async with self.dbh.pool.acquire() as conn:
            return await _conduit_listen(conn, ctx)

    async def claimer_joined(
        self, organization_id: OrganizationID, greeter: UserID, token: UUID
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await send_signal(
                conn,
                BackendEvent.INVITE_STATUS_CHANGED,
                organization_id=organization_id,
                greeter=greeter,
                token=token,
                status_str=InvitationStatus.READY.value,
            )

    async def claimer_left(
        self, organization_id: OrganizationID, greeter: UserID, token: UUID
    ) -> None:
        async with self.dbh.pool.acquire() as conn:
            await send_signal(
                conn,
                BackendEvent.INVITE_STATUS_CHANGED,
                organization_id=organization_id,
                greeter=greeter,
                token=token,
                status_str=InvitationStatus.IDLE.value,
            )
