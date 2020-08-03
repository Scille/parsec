# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
from parsec.core.types import EntryName
from parsec.core.fs import FSBackendOfflineError
from parsec.core.gui.trio_thread import JobResultError
from parsec.core.gui.lang import translate as _
from parsec.core.mountpoint.exceptions import MountpointAlreadyMounted, MountpointNotMounted


async def _get_reencryption_needs(workspace_fs):
    try:
        reenc_needs = await workspace_fs.get_reencryption_need()
    except FSBackendOfflineError as exc:
        raise JobResultError("offline") from exc
    return workspace_fs.workspace_id, reenc_needs


def handle_create_workspace_errors(status):
    if status == "invalid-name":
        return _("TEXT_WORKSPACE_CREATE_NEW_INVALID_NAME")
    else:
        return _("TEXT_WORKSPACE_CREATE_NEW_UNKNOWN_ERROR")


def handle_rename_workspace_errors(status):
    if status == "invalid-name":
        return _("TEXT_WORKSPACE_RENAME_INVALID_NAME")
    else:
        return _("TEXT_WORKSPACE_RENAME_UNKNOWN_ERROR")


async def _do_workspace_create(core, workspace_name):
    try:
        workspace_name = EntryName(workspace_name)
    except ValueError:
        raise JobResultError("invalid-name")
    workspace_id = await core.user_fs.workspace_create(workspace_name)
    return workspace_id


async def _do_workspace_rename(core, workspace_id, new_name, button):
    try:
        new_name = EntryName(new_name)
    except ValueError:
        raise JobResultError("invalid-name")
    try:
        await core.user_fs.workspace_rename(workspace_id, new_name)
        return button, new_name
    except Exception as exc:
        raise JobResultError("rename-error") from exc


async def _do_workspace_list(core):
    workspaces = []

    async def _add_workspacefs(workspace_fs, timestamped):
        ws_entry = workspace_fs.get_workspace_entry()
        try:
            users_roles = await workspace_fs.get_user_roles()
        except FSBackendOfflineError:
            users_roles = {workspace_fs.device.user_id: ws_entry.role}

        try:
            root_info = await workspace_fs.path_info("/")
            files = root_info["children"]
        except FSBackendOfflineError:
            files = []
        workspaces.append((workspace_fs, ws_entry, users_roles, files, timestamped))

    user_manifest = core.user_fs.get_user_manifest()
    available_workspaces = [w for w in user_manifest.workspaces if w.role]
    for count, workspace in enumerate(available_workspaces):
        workspace_id = workspace.id
        workspace_fs = core.user_fs.get_workspace(workspace_id)
        await _add_workspacefs(workspace_fs, timestamped=False)
    worspaces_timestamped_dict = await core.mountpoint_manager.get_timestamped_mounted()
    for (workspace_id, timestamp), workspace_fs in worspaces_timestamped_dict.items():
        await _add_workspacefs(workspace_fs, timestamped=True)

    return workspaces


async def _do_workspace_mount(core, workspace_id, timestamp: pendulum.Pendulum = None):
    try:
        await core.mountpoint_manager.mount_workspace(workspace_id, timestamp)
    except MountpointAlreadyMounted:
        pass


async def _do_workspace_unmount(core, workspace_id, timestamp: pendulum.Pendulum = None):
    try:
        await core.mountpoint_manager.unmount_workspace(workspace_id, timestamp)
    except MountpointNotMounted:
        pass
