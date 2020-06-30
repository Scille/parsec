# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from PyQt5 import QtCore

from parsec.backend.backend_events import BackendEvent
from parsec.core.invite_claim import invite_and_create_device
from parsec.core.types import BackendOrganizationClaimDeviceAddr
from parsec.event_bus import MetaEvent
from tests.common import addr_with_device_subdomain
from tests.open_tcp_stream_mock_wrapper import offline


@pytest.fixture
async def alice_invite(running_backend, backend, alice):
    device_id = alice.user_id.to_device_id("pc1")
    # Modify address subdomain to be able to switch it offline whithout
    # disconnecting the inviter
    organization_addr = addr_with_device_subdomain(alice.organization_addr, device_id)
    invitation = {
        "addr": BackendOrganizationClaimDeviceAddr.build(organization_addr, device_id, "123456"),
        "token": "123456",
        "user_id": device_id.user_id,
        "device_name": device_id.device_name,
        "password": "S3cr3tP@ss",
    }

    async def _invite():
        await invite_and_create_device(alice, invitation["device_name"], invitation["token"])

    async with trio.open_service_nursery() as nursery:
        with backend.event_bus.listen() as spy:
            nursery.start_soon(_invite)
            await spy.wait_with_timeout(
                MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.DEVICE_CLAIMED}
            )

            yield invitation

            nursery.cancel_scope.cancel()


async def _gui_ready_for_claim(aqtbot, gui, invitation, monkeypatch, qt_thread_gateway):
    def open_dialog():
        monkeypatch.setattr(
            "parsec.core.gui.main_window.get_text_input",
            lambda *args, **kwargs: (invitation["addr"].to_url()),
        )
        gui._on_claim_device_clicked()

    await qt_thread_gateway.send_action(open_dialog)

    dialog = None
    for win in gui.children():
        if win.objectName() == "GreyedDialog":
            dialog = win
            break
    assert dialog is not None
    claim_w = dialog.center_widget
    assert claim_w is not None

    await aqtbot.key_clicks(claim_w.line_edit_password, invitation.get("password", ""))
    await aqtbot.key_clicks(claim_w.line_edit_password_check, invitation.get("password", ""))
    return claim_w


@pytest.mark.skip("Uncertainties")
@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_missing_fields(
    aqtbot, gui_factory, autoclose_dialog, core_config, monkeypatch, qt_thread_gateway
):
    gui = await gui_factory()

    def open_dialog():
        monkeypatch.setattr(
            "parsec.core.gui.main_window.get_text_input",
            lambda *args, **kwargs: (
                "parsec://host/org?action=claim_device&device_id=test@test&no_ssl=true&rvk=CMT42NY7MVLO746AI6XOU4PWJDFWYHHEPYWOAVDJKSAP6QN6FYPAssss"
            ),
        )
        gui._on_claim_device_clicked()

    await qt_thread_gateway.send_action(open_dialog)

    dialog = None
    for win in gui.children():
        if win.objectName() == "GreyedDialog":
            dialog = win
            break
    assert dialog is not None

    rdw = dialog.center_widget
    assert rdw is not None

    assert rdw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(rdw.line_edit_token, "token")
    assert rdw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(rdw.line_edit_password, "passwor")
    assert rdw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(rdw.line_edit_password, "d")
    assert rdw.button_claim.isEnabled() is False

    await aqtbot.key_clicks(rdw.line_edit_password_check, "password")
    assert rdw.button_claim.isEnabled() is True

    await aqtbot.key_click(rdw.line_edit_password, QtCore.Qt.Key_Backspace)
    assert rdw.button_claim.isEnabled() is False


@pytest.mark.skip("Uncertainties")
@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device(
    aqtbot, gui, autoclose_dialog, alice_invite, monkeypatch, qt_thread_gateway
):
    claim_w = await _gui_ready_for_claim(aqtbot, gui, alice_invite, monkeypatch, qt_thread_gateway)
    async with aqtbot.wait_signal(claim_w.claim_success):
        await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)
    assert len(autoclose_dialog.dialogs) == 2
    assert autoclose_dialog.dialogs[1][0] == ""
    assert autoclose_dialog.dialogs[1][1] == "Your device was successfully registered!"


@pytest.mark.skip("Uncertainties")
@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_offline(
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


@pytest.mark.skip("NOP")
@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_unknown_error(
    monkeypatch, aqtbot, gui, autoclose_dialog, alice_invite, qt_thread_gateway
):
    claim_w = await _gui_ready_for_claim(aqtbot, gui, alice_invite, monkeypatch, qt_thread_gateway)

    async def _broken(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr("parsec.core.gui.claim_device_widget.core_claim_device", _broken)

    async with aqtbot.wait_signal(claim_w.claim_error):
        await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)
    assert len(autoclose_dialog.dialogs) == 2
    assert autoclose_dialog.dialogs[1][0] == ""
    assert autoclose_dialog.dialogs[1][1] == "RENOP"


@pytest.mark.skip("Nop")
@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_with_start_arg(event_bus, core_config, gui_factory):
    start_arg = "parsec://parsec.example.com/my_org?action=claim_device&device_id=John%40pc&rvk=P25GRG3XPSZKBEKXYQFBOLERWQNEDY3AO43MVNZCLPXPKN63JRYQssss&token=1234ABCD"

    gui = await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=start_arg)
    dialog = None
    for win in gui.children():
        if win.objectName() == "GreyedDialog":
            dialog = win
            break

    assert dialog is not None
    assert dialog.center_widget.objectName() == "ClaimDeviceWidget"


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_with_bad_start_arg(
    event_bus, core_config, gui_factory, autoclose_dialog
):
    bad_start_arg = "parsec://parsec.example.com/my_org?action=dummy&device_id=John%40pc&rvk=P25GRG3XPSZKBEKXYQFBOLERWQNEDY3AO43MVNZCLPXPKN63JRYQssss&token=1234ABCD"

    _ = await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=bad_start_arg)

    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "The URL is invalid."
