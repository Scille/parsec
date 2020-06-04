# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from PyQt5 import QtCore, QtWidgets

from parsec.api.protocol import DeviceID
from parsec.core.types import BackendOrganizationClaimUserAddr
from parsec.core.invite_claim import invite_and_create_user

from tests.common import addr_with_device_subdomain
from tests.open_tcp_stream_mock_wrapper import offline


@pytest.fixture
async def alice_invite(running_backend, backend, alice):
    device_id = DeviceID("Zack@pc1")
    # Modify address subdomain to be able to switch it offline whithout
    # disconnecting the inviter
    organization_addr = addr_with_device_subdomain(alice.organization_addr, device_id)
    invitation = {
        "addr": BackendOrganizationClaimUserAddr.build(organization_addr, "Zack", "123456"),
        "token": "123456",
        "user_id": device_id.user_id,
        "device_name": device_id.device_name,
        "password": "S3cr3tP@ss",
    }

    async def _invite():
        await invite_and_create_user(alice, invitation["user_id"], invitation["token"], True)

    async with trio.open_service_nursery() as nursery:
        with backend.event_bus.listen() as spy:
            nursery.start_soon(_invite)
            await spy.wait_with_timeout("event.connected", {"event_name": "user.claimed"})

            yield invitation

            nursery.cancel_scope.cancel()


async def _gui_ready_for_claim(aqtbot, gui, invitation, monkeypatch, qt_thread_gateway):
    def open_dialog():
        monkeypatch.setattr(
            "parsec.core.gui.main_window.get_text_input",
            lambda *args, **kwargs: (invitation["addr"].to_url()),
        )
        gui._on_claim_user_clicked()
        QtWidgets.QApplication.processEvents()
        dialog = None
        for win in gui.children():
            if win.objectName() == "GreyedDialog":
                dialog = win
                break
        assert dialog is not None
        w = dialog.center_widget
        assert w is not None
        return w

    claim_w = await qt_thread_gateway.send_action(open_dialog)

    await aqtbot.key_clicks(claim_w.line_edit_device, invitation.get("device_name", ""))
    await aqtbot.key_clicks(claim_w.line_edit_password, invitation.get("password", ""))
    await aqtbot.key_clicks(claim_w.line_edit_password_check, invitation.get("password", ""))
    return claim_w


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_user_missing_fields(
    aqtbot, gui_factory, autoclose_dialog, core_config, monkeypatch, qt_thread_gateway
):
    gui = await gui_factory()

    def open_dialog():
        monkeypatch.setattr(
            "parsec.core.gui.main_window.get_text_input",
            lambda *args, **kwargs: (
                "parsec://host/org?action=claim_user&no_ssl=true&rvk=CMT42NY7MVLO746AI6XOU4PWJDFWYHHEPYWOAVDJKSAP6QN6FYPAssss&user_id=test"
            ),
        )
        gui._on_claim_user_clicked()
        QtWidgets.QApplication.processEvents()
        dialog = None
        for win in gui.children():
            if win.objectName() == "GreyedDialog":
                dialog = win
                break
        else:
            raise RuntimeError("Greyed dialog not found")
        assert dialog is not None
        w = dialog.center_widget
        assert w is not None
        return w

    ruw = await qt_thread_gateway.send_action(open_dialog)

    assert ruw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(ruw.line_edit_device, "device")
    assert ruw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(ruw.line_edit_token, "token")
    assert ruw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(ruw.line_edit_password, "passwor")
    assert ruw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(ruw.line_edit_password, "d")
    assert ruw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(ruw.line_edit_password_check, "password")
    assert ruw.button_claim.isEnabled() is True

    await aqtbot.key_click(ruw.line_edit_password, QtCore.Qt.Key_Backspace)
    assert ruw.button_claim.isEnabled() is False


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_user(
    aqtbot, gui, autoclose_dialog, alice_invite, monkeypatch, qt_thread_gateway
):
    claim_w = await _gui_ready_for_claim(aqtbot, gui, alice_invite, monkeypatch, qt_thread_gateway)

    autoclose_dialog.dialogs = []
    async with aqtbot.wait_signal(claim_w.claim_success):
        await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)
    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == ""
    assert (
        autoclose_dialog.dialogs[0][1]
        == "The user has been successfully created! You can now log in."
    )


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_user_offline(
    aqtbot, gui, autoclose_dialog, running_backend, alice_invite, monkeypatch, qt_thread_gateway
):
    claim_w = await _gui_ready_for_claim(aqtbot, gui, alice_invite, monkeypatch, qt_thread_gateway)

    with offline(alice_invite["addr"]):
        async with aqtbot.wait_signal(claim_w.claim_error):
            await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)

    assert len(autoclose_dialog.dialogs) == 2
    assert autoclose_dialog.dialogs[1][0] == "Error"
    assert (
        autoclose_dialog.dialogs[1][1]
        == "The server is offline or you have no access to the internet."
    )


@pytest.mark.skip("TODO: investigate *_unknow_error tests")
@pytest.mark.gui
@pytest.mark.trio
async def test_claim_user_unknown_error(
    monkeypatch, aqtbot, gui, autoclose_dialog, alice_invite, qt_thread_gateway
):
    claim_w = await _gui_ready_for_claim(aqtbot, gui, alice_invite, monkeypatch, qt_thread_gateway)

    assert claim_w is not None

    async def _broken(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr("parsec.core.gui.claim_user_widget.core_claim_user", _broken)

    async with aqtbot.wait_signal(claim_w.claim_error):
        await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)
    assert len(autoclose_dialog.dialogs) == 2
    assert autoclose_dialog.dialogs[1][0] == "Error"
    assert autoclose_dialog.dialogs[1][1] == "An unknown error occurred while registering the user."


@pytest.mark.skip("TODO: investigate *_with_start_arg tests")
@pytest.mark.gui
@pytest.mark.trio
async def test_claim_user_with_start_arg(aqtbot, event_bus, core_config, gui_factory):
    start_arg = "parsec://parsec.example.com/my_org?action=claim_user&rvk=P25GRG3XPSZKBEKXYQFBOLERWQNEDY3AO43MVNZCLPXPKN63JRYQssss&token=1234ABCD&user_id=John"

    gui = await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=start_arg)

    dialog = None
    for win in gui.children():
        if win.objectName() == "GreyedDialog":
            dialog = win
            break

    assert dialog is not None
    assert dialog.center_widget.objectName() == "ClaimUserWidget"
    async with aqtbot.wait_signal(dialog.finished):
        await aqtbot.mouse_click(dialog.button_close, QtCore.Qt.LeftButton)
    assert dialog.result() == QtWidgets.QDialog.Rejected


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_user_with_bad_start_arg(event_bus, core_config, gui_factory, autoclose_dialog):
    bad_start_arg = "parsec://parsec.example.com/my_org?action=dummy&rvk=P25GRG3XPSZKBEKXYQFBOLERWQNEDY3AO43MVNZCLPXPKN63JRYQssss&token=1234ABCD&user_id=John"

    await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=bad_start_arg)

    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "The URL is invalid."
