# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from PyQt5 import QtCore

from parsec.core.invite_claim import invite_and_create_device


@pytest.fixture
async def alice_invite(running_backend, backend, alice):
    invitation = {
        "addr": alice.organization_addr,
        "token": "123456",
        "user_id": alice.user_id,
        "device_name": "pc1",
        "password": "S3cr3tP@ss",
    }

    async def _invite():
        await invite_and_create_device(alice, invitation["device_name"], invitation["token"])

    async with trio.open_nursery() as nursery:
        with backend.event_bus.listen() as spy:
            nursery.start_soon(_invite)
            await spy.wait("event.connected", kwargs={"event_name": "device.claimed"})

            yield invitation

            nursery.cancel_scope.cancel()


async def _gui_ready_for_claim(aqtbot, gui, invitation):
    login_w = gui.test_get_login_widget()
    claim_w = gui.test_get_claim_device_widget()
    assert login_w is not None
    assert claim_w is None

    await aqtbot.mouse_click(login_w.button_register_device_instead, QtCore.Qt.LeftButton)
    claim_w = gui.test_get_claim_device_widget()
    assert claim_w is not None

    await aqtbot.key_clicks(claim_w.line_edit_login, invitation.get("user_id", ""))
    # Device name defaults to machine name
    default_device_name = claim_w.line_edit_device.text()
    assert default_device_name
    for _ in range(len(default_device_name)):
        await aqtbot.key_press(claim_w.line_edit_device, QtCore.Qt.Key_Backspace)
    await aqtbot.key_clicks(claim_w.line_edit_device, invitation.get("device_name", ""))
    await aqtbot.key_clicks(claim_w.line_edit_token, invitation.get("token", ""))
    await aqtbot.key_clicks(claim_w.line_edit_url, invitation.get("addr", ""))
    await aqtbot.key_clicks(claim_w.line_edit_password, invitation.get("password", ""))
    await aqtbot.key_clicks(claim_w.line_edit_password_check, invitation.get("password", ""))


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device(aqtbot, gui, autoclose_dialog, alice_invite):
    await _gui_ready_for_claim(aqtbot, gui, alice_invite)
    claim_w = gui.test_get_claim_device_widget()
    async with aqtbot.wait_signal(claim_w.device_claimed):
        await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)
    assert autoclose_dialog.dialogs == [
        (
            "Warning",
            "Please CAREFULLY remind your password. Losing a password means losing the "
            "data if you have one device, or if it has not been synced yet.",
        ),
        ("Information", "The device has been created. You can now log in."),
    ]


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_offline(aqtbot, gui, autoclose_dialog, running_backend, alice_invite):
    await _gui_ready_for_claim(aqtbot, gui, alice_invite)
    claim_w = gui.test_get_claim_device_widget()

    with running_backend.offline():
        async with aqtbot.wait_signal(claim_w.claim_error):
            await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)

    assert autoclose_dialog.dialogs == [
        (
            "Warning",
            "Please CAREFULLY remind your password. Losing a password means losing the "
            "data if you have one device, or if it has not been synced yet.",
        ),
        ("Error", "Cannot claim this device."),
    ]


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_unknown_error(monkeypatch, aqtbot, gui, autoclose_dialog, alice_invite):
    await _gui_ready_for_claim(aqtbot, gui, alice_invite)
    claim_w = gui.test_get_claim_device_widget()

    async def _broken(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr("parsec.core.gui.claim_device_widget.core_claim_device", _broken)

    async with aqtbot.wait_signal(claim_w.claim_error):
        await aqtbot.mouse_click(claim_w.button_claim, QtCore.Qt.LeftButton)
    assert autoclose_dialog.dialogs == [
        (
            "Warning",
            "Please CAREFULLY remind your password. Losing a password means losing the "
            "data if you have one device, or if it has not been synced yet.",
        ),
        ("Error", "Cannot claim this device."),
    ]
    # TODO: Make sure a log is emitted
