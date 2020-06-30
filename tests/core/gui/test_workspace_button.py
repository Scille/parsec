# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from PyQt5 import QtCore

from parsec.core.gui.lang import switch_language
from parsec.core.gui.workspace_button import WorkspaceButton
from parsec.core.types import WorkspaceRole


@pytest.fixture
@pytest.mark.trio
async def workspace_fs(alice_user_fs, running_backend):
    wid = await alice_user_fs.workspace_create("Workspace")
    workspace = alice_user_fs.get_workspace(wid)
    return workspace


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button(qtbot, workspace_fs, core_config):
    switch_language(core_config, "en")

    roles = {workspace_fs.device.user_id: WorkspaceRole.OWNER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=[],
    )
    qtbot.addWidget(w)
    w.show()

    assert w.widget_empty.isVisible() is True
    assert w.widget_files.isVisible() is False
    assert w.label_owner.isVisible() is True
    assert w.label_shared.isVisible() is False
    assert w.name == "Workspace"
    assert w.label_title.text().startswith("Workspace")
    assert w.label_title.toolTip() == "Workspace (private)"


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button_owned_by(qtbot, workspace_fs, core_config, bob):
    switch_language(core_config, "en")

    roles = {bob.user_id: WorkspaceRole.OWNER, workspace_fs.device.user_id: WorkspaceRole.READER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=[],
    )

    qtbot.addWidget(w)
    w.show()
    assert w.widget_empty.isVisible() is True
    assert w.widget_files.isVisible() is False
    assert w.label_owner.isVisible() is False
    assert w.label_shared.isVisible() is True
    assert w.name == "Workspace"
    assert w.label_title.text().startswith("Workspace")
    assert w.label_title.toolTip() == "Workspace (owned by bob)"


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button_shared_with(qtbot, workspace_fs, core_config, bob):
    switch_language(core_config, "en")

    roles = {bob.user_id: WorkspaceRole.READER, workspace_fs.device.user_id: WorkspaceRole.OWNER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=[],
    )

    qtbot.addWidget(w)
    w.show()
    assert w.widget_empty.isVisible() is True
    assert w.widget_files.isVisible() is False
    assert w.label_owner.isVisible() is True
    assert w.label_shared.isVisible() is True
    assert w.name == "Workspace"
    assert w.label_title.text().startswith("Workspace")
    assert w.label_title.toolTip() == "Workspace (shared with bob)"


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button_files(qtbot, workspace_fs, core_config):
    switch_language(core_config, "en")

    roles = {workspace_fs.device.user_id: WorkspaceRole.OWNER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=["File1.txt", "File2.txt", "Dir1"],
    )

    qtbot.addWidget(w)
    w.show()
    assert w.widget_empty.isVisible() is False
    assert w.widget_files.isVisible() is True
    assert w.label_owner.isVisible() is True
    assert w.label_shared.isVisible() is False
    assert w.name == "Workspace"
    assert w.file1_name.text() == "File1.txt"
    assert w.file2_name.text() == "File2.txt"
    assert w.file3_name.text() == "Dir1"
    assert w.file4_name.text() == ""


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button_clicked(qtbot, workspace_fs, core_config):
    switch_language(core_config, "en")

    roles = {workspace_fs.device.user_id: WorkspaceRole.OWNER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=[],
    )

    qtbot.addWidget(w)
    with qtbot.waitSignal(w.clicked, timeout=500) as blocker:
        qtbot.mouseClick(w, QtCore.Qt.LeftButton)
    assert blocker.args == [workspace_fs]


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button_share_clicked(qtbot, workspace_fs, core_config):
    switch_language(core_config, "en")

    roles = {workspace_fs.device.user_id: WorkspaceRole.OWNER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=[],
    )
    qtbot.addWidget(w)
    with qtbot.waitSignal(w.share_clicked, timeout=500) as blocker:
        qtbot.mouseClick(w.button_share, QtCore.Qt.LeftButton)
    assert blocker.args == [workspace_fs]


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button_rename_clicked(qtbot, workspace_fs, core_config):
    switch_language(core_config, "en")

    roles = {workspace_fs.device.user_id: WorkspaceRole.OWNER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=[],
    )
    qtbot.addWidget(w)
    with qtbot.waitSignal(w.rename_clicked, timeout=500) as blocker:
        qtbot.mouseClick(w.button_rename, QtCore.Qt.LeftButton)
    assert blocker.args == [w]


@pytest.mark.gui
@pytest.mark.trio
async def test_workspace_button_delete_clicked(qtbot, workspace_fs, core_config):
    switch_language(core_config, "en")

    roles = {workspace_fs.device.user_id: WorkspaceRole.OWNER}
    w = WorkspaceButton(
        workspace_name="Workspace",
        workspace_fs=workspace_fs,
        users_roles=roles,
        is_mounted=True,
        files=[],
    )
    qtbot.addWidget(w)
    with qtbot.waitSignal(w.delete_clicked, timeout=500) as blocker:
        qtbot.mouseClick(w.button_delete, QtCore.Qt.LeftButton)
    assert blocker.args == [workspace_fs]
