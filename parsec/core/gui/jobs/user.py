# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS
from parsec.api.protocol.invite import InvitationType
from parsec.core.backend_connection.exceptions import BackendNotAvailable, BackendConnectionError
from parsec.core.gui.trio_thread import JobResultError
from parsec.core.gui.lang import translate as _


async def _do_revoke_user(core, user_info):
    try:
        await core.revoke_user(user_info.user_id)
        user_info = await core.get_user_info(user_info.user_id)
        return user_info
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    except BackendConnectionError as exc:
        raise JobResultError("error") from exc


async def _do_list_users_and_invitations(core):
    try:
        # TODO: handle pagination ! (currently we only display the first 100 users...)
        users, total = await core.find_humans()
        invitations = await core.list_invitations()
        return users, [inv for inv in invitations if inv["type"] == InvitationType.USER]
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    except BackendConnectionError as exc:
        raise JobResultError("error") from exc


async def _do_cancel_invitation(core, token):
    try:
        await core.delete_invitation(token=token)
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    except BackendConnectionError as exc:
        raise JobResultError("error") from exc


async def _do_invite_user(core, email):
    try:
        return await core.new_user_invitation(email=email, send_email=True)
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    except BackendConnectionError as exc:
        raise JobResultError("error") from exc


def handle_invite_errors(status):
    if status == "offline":
        return _("TEXT_INVITE_USER_INVITE_OFFLINE")
    else:
        return _("TEXT_INVITE_USER_INVITE_ERROR")
