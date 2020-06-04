# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from PyQt5 import QtCore, QtWidgets

from parsec.core.types import BackendOrganizationBootstrapAddr


async def _gui_ready_for_bootstrap(aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway):
    org_id = "NewOrg"
    org_token = "123456"
    user_id = "Zack"
    device_name = "pc1"
    password = "S3cr3tP@ss"
    organization_addr = BackendOrganizationBootstrapAddr.build(
        running_backend.addr, org_id, org_token
    )

    # Create organization in the backend
    await running_backend.backend.organization.create(org_id, org_token)

    def open_dialog():
        monkeypatch.setattr(
            "parsec.core.gui.main_window.get_text_input",
            lambda *args, **kwargs: (organization_addr.to_url()),
        )
        gui._on_bootstrap_org_clicked()

    await qt_thread_gateway.send_action(open_dialog)

    dialog = None
    for win in gui.children():
        if win.objectName() == "GreyedDialog":
            dialog = win
            break
    else:
        raise RuntimeError("GreyedDialog not found")

    bw = dialog.center_widget
    await aqtbot.key_clicks(bw.line_edit_login, user_id)
    await aqtbot.key_clicks(bw.line_edit_password, password)
    await aqtbot.key_clicks(bw.line_edit_password_check, password)
    await aqtbot.key_clicks(bw.line_edit_device, device_name)
    return bw


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization(
    aqtbot, running_backend, gui, autoclose_dialog, monkeypatch, qt_thread_gateway
):
    bw = await _gui_ready_for_bootstrap(
        aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway
    )

    async with aqtbot.wait_signal(bw.bootstrap_success):
        await aqtbot.mouse_click(bw.button_bootstrap, QtCore.Qt.LeftButton)

    assert len(autoclose_dialog.dialogs) == 2
    assert autoclose_dialog.dialogs[1][0] == ""
    assert (
        autoclose_dialog.dialogs[1][1]
        == "The organization was successfully bootstrapped! You can now log in."
    )


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_org_missing_fields(
    aqtbot, qt_thread_gateway, gui, autoclose_dialog, core_config, monkeypatch
):
    def open_dialog():
        monkeypatch.setattr(
            "parsec.core.gui.main_window.get_text_input",
            lambda *args, **kwargs: (
                "parsec://host/org?action=bootstrap_organization&no_ssl=true&token=2eead2c011e4ad9878ffc5854a38b395ecd22279b86994f804bdfc7cad81ed66"
            ),
        )
        gui._on_bootstrap_org_clicked()

    await qt_thread_gateway.send_action(open_dialog)

    dialog = None
    for win in gui.children():
        if win.objectName() == "GreyedDialog":
            dialog = win
            break
    assert dialog is not None

    bw = dialog.center_widget
    assert bw is not None

    assert bw.button_bootstrap.isEnabled() is False

    await aqtbot.key_clicks(bw.line_edit_login, "login")
    assert bw.button_bootstrap.isEnabled() is False

    await aqtbot.key_clicks(bw.line_edit_device, "device")
    assert bw.button_bootstrap.isEnabled() is False

    await aqtbot.key_clicks(bw.line_edit_password, "passwor")
    assert bw.button_bootstrap.isEnabled() is False

    await aqtbot.key_clicks(bw.line_edit_password, "d")
    assert bw.button_bootstrap.isEnabled() is False

    await aqtbot.key_clicks(bw.line_edit_password_check, "password")
    assert bw.button_bootstrap.isEnabled() is True

    await aqtbot.key_click(bw.line_edit_password, QtCore.Qt.Key_Backspace)
    assert bw.button_bootstrap.isEnabled() is False


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_backend_offline(
    aqtbot, running_backend, gui, autoclose_dialog, monkeypatch, qt_thread_gateway
):
    bw = await _gui_ready_for_bootstrap(
        aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway
    )

    with running_backend.offline():
        async with aqtbot.wait_signal(bw.bootstrap_error):
            await aqtbot.mouse_click(bw.button_bootstrap, QtCore.Qt.LeftButton)
        assert len(autoclose_dialog.dialogs) == 2
        assert autoclose_dialog.dialogs[1][0] == "Error"
        assert (
            autoclose_dialog.dialogs[1][1]
            == "The server is offline or you have no access to the internet."
        )


@pytest.mark.skip("TODO: investigate *_unknow_error tests")
@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_unknown_error(
    monkeypatch, aqtbot, running_backend, gui, autoclose_dialog, qt_thread_gateway
):
    bw = await _gui_ready_for_bootstrap(
        aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway
    )

    def _broken(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr(
        "parsec.core.gui.bootstrap_organization_widget.UserCertificateContent", _broken
    )

    async with aqtbot.wait_signal(bw.bootstrap_error):
        await aqtbot.mouse_click(bw.button_bootstrap, QtCore.Qt.LeftButton)
        assert len(autoclose_dialog.dialogs) == 1
        assert autoclose_dialog.dialogs[0][0] == "Error"
        assert (
            autoclose_dialog.dialogs[0][1]
            == "The server is offline or you have no access to the internet."
        )


@pytest.mark.skip(
    "TODO: investigate *_unknow_error tests\n"
    "The dialog doesnt close. Everything goes perfectly fine, but we have to close it manually, even if the wait signal and the button click are performed. No idea."
)
@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_with_start_arg(aqtbot, event_bus, core_config, gui_factory):
    start_arg = "parsec://parsec.example.com/my_org?action=bootstrap_organization&token=1234ABCD"

    gui = await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=start_arg)

    dialog = None
    for win in gui.children():
        if win.objectName() == "GreyedDialog":
            dialog = win
            break

    assert dialog is not None
    assert dialog.center_widget.objectName() == "BootstrapOrganizationWidget"
    async with aqtbot.wait_signal(dialog.finished):
        await aqtbot.mouse_click(dialog.button_close, QtCore.Qt.LeftButton)
    assert dialog.result() == QtWidgets.QDialog.Rejected


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_with_bad_start_arg(
    event_bus, core_config, gui_factory, autoclose_dialog
):
    bad_start_arg = "parsec://parsec.example.com/my_org?action=dummy&token=1234ABCD"

    _ = await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=bad_start_arg)

    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "The URL is invalid."
