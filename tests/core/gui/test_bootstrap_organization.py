# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from PyQt5 import QtCore

from parsec.core.invite.exceptions import InviteNotFoundError, InviteAlreadyUsedError
from parsec.core.types import BackendOrganizationBootstrapAddr


async def _gui_ready_for_bootstrap(aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway):
    org_id = "NewOrg"
    org_token = "123456"
    user_id = "Zack"
    user_email = "zack@host.com"
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

    assert dialog
    bw = dialog.center_widget
    await aqtbot.key_clicks(bw.line_edit_login, user_id)
    await aqtbot.key_clicks(bw.line_edit_email, user_email)
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
        == "You organization <b>NewOrg</b> has been created!<br />\n<br />\n"
        "You will now be automatically logged in.<br />\n<br />\n"
        "To help you start with PARSEC, you can read the "
        '<a href="https://docs.parsec.cloud/en/stable/" title="User guide">user guide</a>.'
    )


@pytest.mark.skip("Failing sometimes")
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

    def dialog_shown():
        dialog = None
        for win in gui.children():
            if win.objectName() == "GreyedDialog":
                dialog = win
                break
        assert dialog is not None

    await aqtbot.wait_until(dialog_shown)

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

    await aqtbot.key_clicks(bw.line_edit_email, "user@host.com")
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


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_invite_already_used(
    monkeypatch, aqtbot, running_backend, gui, autoclose_dialog, qt_thread_gateway
):
    bw = await _gui_ready_for_bootstrap(
        aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway
    )

    def _raise_already_used(*args, **kwargs):
        raise InviteAlreadyUsedError()

    monkeypatch.setattr(
        "parsec.core.gui.bootstrap_organization_widget.bootstrap_organization", _raise_already_used
    )

    def error_shown():
        assert len(autoclose_dialog.dialogs) == 2

    async with aqtbot.wait_signal(bw.bootstrap_error):
        await aqtbot.mouse_click(bw.button_bootstrap, QtCore.Qt.LeftButton)
        await aqtbot.wait_until(error_shown)
        assert autoclose_dialog.dialogs[1][0] == "Error"
        assert autoclose_dialog.dialogs[1][1] == "This organization has already been bootstrapped."


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_invite_not_found(
    monkeypatch, aqtbot, running_backend, gui, autoclose_dialog, qt_thread_gateway
):
    bw = await _gui_ready_for_bootstrap(
        aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway
    )

    def _raise_not_found(*args, **kwargs):
        raise InviteNotFoundError()

    monkeypatch.setattr(
        "parsec.core.gui.bootstrap_organization_widget.bootstrap_organization", _raise_not_found
    )

    def error_shown():
        assert len(autoclose_dialog.dialogs) == 2

    async with aqtbot.wait_signal(bw.bootstrap_error):
        await aqtbot.mouse_click(bw.button_bootstrap, QtCore.Qt.LeftButton)
        await aqtbot.wait_until(error_shown)
        assert autoclose_dialog.dialogs[1][0] == "Error"
        assert (
            autoclose_dialog.dialogs[1][1]
            == "There are no organization to bootstrap with this link."
        )


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_unknown_error(
    monkeypatch, aqtbot, running_backend, gui, autoclose_dialog, qt_thread_gateway
):
    bw = await _gui_ready_for_bootstrap(
        aqtbot, gui, running_backend, monkeypatch, qt_thread_gateway
    )

    def _raise_broken(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr(
        "parsec.core.gui.bootstrap_organization_widget.bootstrap_organization", _raise_broken
    )

    def error_shown():
        assert len(autoclose_dialog.dialogs) == 2

    async with aqtbot.wait_signal(bw.bootstrap_error):
        await aqtbot.mouse_click(bw.button_bootstrap, QtCore.Qt.LeftButton)
        await aqtbot.wait_until(error_shown)
        assert autoclose_dialog.dialogs[1][0] == "Error"
        assert autoclose_dialog.dialogs[1][1] == "Could not bootstrap the organization."


@pytest.mark.gui
@pytest.mark.trio
async def test_bootstrap_organization_with_bad_start_arg(
    event_bus, core_config, gui_factory, autoclose_dialog
):
    bad_start_arg = "parsec://example.com:9999/NewOrg?action=dummy&token=123456&no_ssl=true"

    _ = await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=bad_start_arg)

    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "The link is invalid."
