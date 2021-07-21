# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pytest
from PyQt5 import QtCore
from parsec.core.gui.login_widget import LoginPasswordInputWidget


@pytest.fixture
def catch_password_change_widget(widget_catcher_factory):
    return widget_catcher_factory(
        "parsec.core.gui.password_change_widget.PasswordChangeWidget", timeout=2000
    )


@pytest.mark.gui
@pytest.mark.trio
async def test_change_password_invalid_old_password(
    aqtbot, running_backend, logged_gui, catch_password_change_widget, autoclose_dialog
):
    c_w = logged_gui.test_get_central_widget()
    assert c_w is not None

    # Trigger password change
    c_w.button_user.menu().actions()[0].trigger()

    pc_w = await catch_password_change_widget()

    await aqtbot.key_clicks(pc_w.line_edit_old_password, "0123456789")
    await aqtbot.key_clicks(pc_w.widget_new_password.line_edit_password, "P@ssw0rd2")
    await aqtbot.key_clicks(pc_w.widget_new_password.line_edit_password_check, "P@ssw0rd2")
    await aqtbot.mouse_click(pc_w.button_change, QtCore.Qt.LeftButton)

    assert autoclose_dialog.dialogs == [
        ("Error", "You did not provide the right password for this device.")
    ]


@pytest.mark.gui
@pytest.mark.trio
async def test_change_password_invalid_password_check(
    aqtbot, running_backend, logged_gui, catch_password_change_widget, autoclose_dialog
):
    c_w = logged_gui.test_get_central_widget()

    assert c_w is not None

    # Trigger password change
    c_w.button_user.menu().actions()[0].trigger()

    pc_w = await catch_password_change_widget()

    await aqtbot.key_clicks(pc_w.line_edit_old_password, "P@ssw0rd")
    await aqtbot.key_clicks(pc_w.widget_new_password.line_edit_password, "P@ssw0rd2")
    await aqtbot.key_clicks(pc_w.widget_new_password.line_edit_password_check, "P@ssw0rd3")
    assert not pc_w.button_change.isEnabled()


@pytest.mark.gui
@pytest.mark.trio
async def test_change_password_success(
    aqtbot, running_backend, logged_gui, catch_password_change_widget, autoclose_dialog
):
    c_w = logged_gui.test_get_central_widget()

    assert c_w is not None

    # Trigger password change
    c_w.button_user.menu().actions()[0].trigger()

    pc_w = await catch_password_change_widget()

    await aqtbot.key_clicks(pc_w.line_edit_old_password, "P@ssw0rd")
    await aqtbot.key_clicks(pc_w.widget_new_password.line_edit_password, "P@ssw0rd2")
    await aqtbot.key_clicks(pc_w.widget_new_password.line_edit_password_check, "P@ssw0rd2")
    await aqtbot.mouse_click(pc_w.button_change, QtCore.Qt.LeftButton)

    assert autoclose_dialog.dialogs == [("", "The password has been successfully changed.")]
    autoclose_dialog.reset()

    # Retry to login...
    await logged_gui.test_logout_and_switch_to_login_widget()

    # ...with old password...
    await logged_gui.test_proceed_to_login("P@ssw0rd", error=True)
    assert autoclose_dialog.dialogs == [("Error", "The password is incorrect.")]

    # ...and new password
    l_w = logged_gui.test_get_login_widget()
    password_w = l_w.widget.layout().itemAt(0).widget()
    assert isinstance(password_w, LoginPasswordInputWidget)

    await aqtbot.key_clicks(password_w.line_edit_password, "P@ssw0rd2")

    print(password_w.line_edit_password.text())

    tabw = logged_gui.test_get_tab()

    async with aqtbot.wait_signals([l_w.login_with_password_clicked, tabw.logged_in]):
        await aqtbot.mouse_click(password_w.button_login, QtCore.Qt.LeftButton)

    def _wait_logged_in():
        assert not l_w.isVisible()
        c_w = logged_gui.test_get_central_widget()
        assert c_w.isVisible()

    await aqtbot.wait_until(_wait_logged_in)
