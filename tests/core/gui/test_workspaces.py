# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from PyQt5 import QtCore

from parsec.core.core_events import CoreEvent
from parsec.core.fs import FSWorkspaceNoReadAccess
from parsec.core.local_device import save_device_with_password


@pytest.fixture
async def logged_gui(aqtbot, gui_factory, autoclose_dialog, core_config, alice, bob):
    save_device_with_password(core_config.config_dir, alice, "P@ssw0rd")

    gui = await gui_factory()
    lw = gui.test_get_login_widget()
    tabw = gui.test_get_tab()

    assert lw is not None

    await aqtbot.key_clicks(lw.line_edit_password, "P@ssw0rd")

    async with aqtbot.wait_signals([lw.login_with_password_clicked, tabw.logged_in]):
        await aqtbot.mouse_click(lw.button_login, QtCore.Qt.LeftButton)

    central_widget = gui.test_get_central_widget()
    assert central_widget is not None

    save_device_with_password(core_config.config_dir, bob, "P@ssw0rd")

    yield gui


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize("invalid_name", (False, True))
async def test_add_workspace(aqtbot, running_backend, logged_gui, monkeypatch, invalid_name):
    w_w = logged_gui.test_get_workspaces_widget()

    assert w_w is not None
    async with aqtbot.wait_signal(w_w.list_success):
        pass
    assert w_w.layout_workspaces.count() == 1
    assert w_w.layout_workspaces.itemAt(0).widget().text() == "No workspace has been created yet."

    add_button = w_w.button_add_workspace
    add_button is not None

    workspace_name = ".." if invalid_name else "Workspace1"
    monkeypatch.setattr(
        "parsec.core.gui.workspaces_widget.get_text_input", lambda *args, **kwargs: (workspace_name)
    )

    if invalid_name:
        async with aqtbot.wait_signals([w_w.create_error, w_w.list_success], timeout=2000):
            await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

        assert w_w.layout_workspaces.count() == 1
        assert (
            w_w.layout_workspaces.itemAt(0).widget().text() == "No workspace has been created yet."
        )

    else:
        async with aqtbot.wait_signals([w_w.create_success, w_w.list_success], timeout=2000):
            await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

        assert w_w.layout_workspaces.count() == 1
        wk_button = w_w.layout_workspaces.itemAt(0).widget()
        assert wk_button.name == "Workspace1"


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize("invalid_name", (False, True))
async def test_rename_workspace(aqtbot, running_backend, logged_gui, monkeypatch, invalid_name):
    w_w = logged_gui.test_get_workspaces_widget()

    assert w_w is not None
    async with aqtbot.wait_signal(w_w.list_success):
        pass
    assert w_w.layout_workspaces.count() == 1
    assert w_w.layout_workspaces.itemAt(0).widget().text() == "No workspace has been created yet."

    add_button = w_w.button_add_workspace
    assert add_button is not None

    monkeypatch.setattr(
        "parsec.core.gui.workspaces_widget.get_text_input", lambda *args, **kwargs: ("Workspace1")
    )

    async with aqtbot.wait_signals([w_w.create_success, w_w.list_success], timeout=2000):
        await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

    assert w_w.layout_workspaces.count() == 1
    wk_button = w_w.layout_workspaces.itemAt(0).widget()
    assert wk_button.name == "Workspace1"

    workspace_name = ".." if invalid_name else "Workspace1_Renamed"
    monkeypatch.setattr(
        "parsec.core.gui.workspaces_widget.get_text_input", lambda *args, **kwargs: (workspace_name)
    )

    if invalid_name:
        async with aqtbot.wait_signal(w_w.rename_error):
            await aqtbot.mouse_click(wk_button.button_rename, QtCore.Qt.LeftButton)
        assert wk_button.name == "Workspace1"

    else:
        async with aqtbot.wait_signal(w_w.rename_success):
            await aqtbot.mouse_click(wk_button.button_rename, QtCore.Qt.LeftButton)
        assert wk_button.name == "Workspace1_Renamed"


@pytest.mark.skip("No notification center at the moment")
@pytest.mark.gui
@pytest.mark.trio
async def test_mountpoint_remote_error_event(aqtbot, running_backend, logged_gui):
    c_w = logged_gui.test_get_central_widget()

    async with aqtbot.wait_signal(c_w.new_notification):
        c_w.event_bus.send(
            CoreEvent.MOUNTPOINT_REMOTE_ERROR,
            exc=FSWorkspaceNoReadAccess("Cannot get workspace roles: no read access"),
            path="/foo",
            operation="open",
        )
    msg_widget = c_w.notification_center.widget_layout.layout().itemAt(0).widget()
    assert (
        msg_widget.message
        == 'Cannot access "/foo" from the server given you lost read access to the workspace.'
    )

    async with aqtbot.wait_signal(c_w.new_notification):
        c_w.event_bus.send(
            CoreEvent.MOUNTPOINT_UNHANDLED_ERROR,
            exc=RuntimeError("D'Oh !"),
            path="/bar",
            operation="unlink",
        )
    msg_widget = c_w.notification_center.widget_layout.layout().itemAt(0).widget()
    assert (
        msg_widget.message
        == 'Unexpected error while performing "unlink" operation on "/bar": D\'Oh !.'
    )
