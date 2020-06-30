# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import patch

import pytest
from PyQt5 import QtCore

from parsec.core.gui.custom_dialogs import GreyedDialog
from parsec.core.gui.invite_device_widget import InviteDeviceWidget
from parsec.core.invite_claim import claim_device as actual_claim_device
from parsec.core.local_device import save_device_with_password
from parsec.core.types import BackendOrganizationClaimDeviceAddr
from parsec.utils import trio_run


@pytest.fixture
async def logged_gui(aqtbot, gui_factory, autoclose_dialog, core_config, alice):
    # Create an existing device before starting the gui
    password = "P@ssw0rd"
    save_device_with_password(core_config.config_dir, alice, password)

    gui = await gui_factory()
    lw = gui.test_get_login_widget()
    tabw = gui.test_get_tab()

    assert lw is not None

    await aqtbot.key_clicks(lw.line_edit_password, password)

    async with aqtbot.wait_signals([lw.login_with_password_clicked, tabw.logged_in]):
        await aqtbot.mouse_click(lw.button_login, QtCore.Qt.LeftButton)

    central_widget = gui.test_get_central_widget()
    assert central_widget is not None

    await aqtbot.mouse_click(central_widget.menu.button_devices, QtCore.Qt.LeftButton)
    yield gui


@pytest.mark.gui
@pytest.mark.trio
async def test_register_device_open_modal(aqtbot, logged_gui, running_backend):
    d_w = logged_gui.test_get_devices_widget()

    assert d_w is not None
    async with aqtbot.wait_signal(d_w.list_success):
        pass

    with patch("parsec.core.gui.devices_widget.InviteDeviceWidget.exec_modal") as register_mock:
        await aqtbot.mouse_click(d_w.button_add_device, QtCore.Qt.LeftButton)
        register_mock.assert_called_once_with(parent=d_w, jobs_ctx=d_w.jobs_ctx, core=d_w.core)


@pytest.mark.skip("Freezes don't know why")
@pytest.mark.gui
@pytest.mark.trio
async def test_register_device_modal_ok(
    aqtbot, logged_gui, running_backend, qt_thread_gateway, alice, autoclose_dialog, monkeypatch
):
    d_w = logged_gui.test_get_devices_widget()
    assert d_w is not None

    async def _claim_device(token, addr, device_id, password, config):
        device = await actual_claim_device(
            organization_addr=addr,
            new_device_id=device_id,
            token=token,
            keepalive=config.backend_connection_keepalive,
        )
        save_device_with_password(config.config_dir, device, password)

    def run_dialog():
        modal = InviteDeviceWidget(parent=d_w, jobs_ctx=d_w.jobs_ctx, core=d_w.core)
        modal.show()
        assert not modal.line_edit_token.text()
        assert not modal.line_edit_url.text()
        aqtbot.qtbot.keyClicks(modal.line_edit_device_name, "new_device")
        aqtbot.qtbot.mouseClick(modal.button_register, QtCore.Qt.LeftButton)
        assert modal.line_edit_url.text()
        assert modal.line_edit_device_name.text() == "new_device"
        assert modal.line_edit_token.text()
        url = BackendOrganizationClaimDeviceAddr.from_url(modal.line_edit_url.text())

        with aqtbot.qtbot.waitSignal(modal.device_registered):
            trio_run(
                _claim_device,
                modal.line_edit_token.text(),
                url.to_organization_addr(),
                url.device_id,
                "P@ssw0rd!",
                logged_gui.config,
            )
        assert len(autoclose_dialog.dialogs) == 1

        assert autoclose_dialog.dialogs[0][0] == ""
        assert autoclose_dialog.dialogs[0][1].label_message.text() == ""

    await qt_thread_gateway.send_action(run_dialog)


@pytest.mark.gui
@pytest.mark.trio
async def test_register_device_modal_invalid_device_name(
    aqtbot, logged_gui, running_backend, qt_thread_gateway, alice, autoclose_dialog
):
    d_w = logged_gui.test_get_devices_widget()
    assert d_w is not None

    def run_dialog():
        with patch("parsec.core.gui.invite_device_widget.DeviceName") as type_mock:
            type_mock.side_effect = ValueError()
            modal = InviteDeviceWidget(parent=d_w, jobs_ctx=d_w.jobs_ctx, core=d_w.core)
            modal.show()
            assert not modal.line_edit_token.text()
            assert not modal.line_edit_url.text()
            aqtbot.qtbot.keyClicks(modal.line_edit_device_name, "new_device")
            with aqtbot.qtbot.waitSignal(modal.registration_error):
                aqtbot.qtbot.mouseClick(modal.button_register, QtCore.Qt.LeftButton)

    await qt_thread_gateway.send_action(run_dialog)
    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "The device name is invalid."


@pytest.mark.gui
@pytest.mark.trio
async def test_register_device_modal_cancel(
    aqtbot, logged_gui, running_backend, qt_thread_gateway, alice, autoclose_dialog
):
    d_w = logged_gui.test_get_devices_widget()
    assert d_w is not None

    def run_dialog():
        w = InviteDeviceWidget(jobs_ctx=d_w.jobs_ctx, core=d_w.core)
        d = GreyedDialog(w, title="Title", parent=d_w)
        d.show()
        assert not w.line_edit_token.text()
        assert not w.line_edit_url.text()
        aqtbot.qtbot.keyClicks(w.line_edit_device_name, "new_device")
        aqtbot.qtbot.mouseClick(w.button_register, QtCore.Qt.LeftButton)
        assert w.line_edit_url.text()
        assert w.line_edit_device_name.text() == "new_device"
        assert w.line_edit_token.text()
        with aqtbot.qtbot.waitSignal(w.registration_error):
            aqtbot.qtbot.mouseClick(d.button_close, QtCore.Qt.LeftButton)
        assert not w.widget_registration.isVisible()
        assert not d.isVisible()

    await qt_thread_gateway.send_action(run_dialog)


@pytest.mark.gui
@pytest.mark.trio
async def test_register_device_modal_offline(
    aqtbot, logged_gui, running_backend, qt_thread_gateway, alice, autoclose_dialog
):
    d_w = logged_gui.test_get_devices_widget()
    assert d_w is not None

    def run_dialog():
        modal = InviteDeviceWidget(parent=d_w, jobs_ctx=d_w.jobs_ctx, core=d_w.core)
        modal.show()
        assert not modal.line_edit_token.text()
        assert not modal.line_edit_url.text()
        aqtbot.qtbot.keyClicks(modal.line_edit_device_name, "new_device")

        with aqtbot.qtbot.waitSignal(modal.registration_error):
            aqtbot.qtbot.mouseClick(modal.button_register, QtCore.Qt.LeftButton)

    with running_backend.offline():
        await qt_thread_gateway.send_action(run_dialog)

    assert len(autoclose_dialog.dialogs)
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert (
        autoclose_dialog.dialogs[0][1]
        == "The server is offline or you have no access to the internet."
    )


@pytest.mark.gui
@pytest.mark.trio
async def test_register_device_modal_already_registered(
    aqtbot, logged_gui, running_backend, qt_thread_gateway, alice, autoclose_dialog
):
    d_w = logged_gui.test_get_devices_widget()
    assert d_w is not None

    def run_dialog():
        modal = InviteDeviceWidget(parent=d_w, jobs_ctx=d_w.jobs_ctx, core=d_w.core)
        modal.show()
        assert not modal.line_edit_token.text()
        assert not modal.line_edit_url.text()
        aqtbot.qtbot.keyClicks(modal.line_edit_device_name, "dev1")
        with aqtbot.qtbot.waitSignal(modal.registration_error):
            aqtbot.qtbot.mouseClick(modal.button_register, QtCore.Qt.LeftButton)

    await qt_thread_gateway.send_action(run_dialog)
    assert len(autoclose_dialog.dialogs)
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "This device name is already in use."


@pytest.mark.gui
@pytest.mark.trio
async def test_register_device_unknown_error(
    monkeypatch, aqtbot, logged_gui, running_backend, qt_thread_gateway, alice, autoclose_dialog
):
    d_w = logged_gui.test_get_devices_widget()
    assert d_w is not None

    async def _broken(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr(
        "parsec.core.gui.invite_device_widget.core_invite_and_create_device", _broken
    )

    def run_dialog():
        modal = InviteDeviceWidget(parent=d_w, jobs_ctx=d_w.jobs_ctx, core=d_w.core)
        modal.show()
        aqtbot.qtbot.keyClicks(modal.line_edit_device_name, "new_device")
        with aqtbot.qtbot.waitSignal(modal.registration_error):
            aqtbot.qtbot.mouseClick(modal.button_register, QtCore.Qt.LeftButton)
        assert len(autoclose_dialog.dialogs)
        assert autoclose_dialog.dialogs[0][0] == "Error"
        assert autoclose_dialog.dialogs[0][1] == "The device could not be invited."

    await qt_thread_gateway.send_action(run_dialog)
    # TODO: Make sure a log is emitted


# TODO test with timeout
