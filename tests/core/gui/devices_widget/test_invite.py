# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pytest
import trio
from PyQt5 import QtCore
from unittest.mock import ANY

from parsec.core.backend_connection import backend_invited_cmds_factory
from parsec.core.invite import claimer_retrieve_info
from parsec.core.gui.greet_device_widget import (
    GreetDeviceInstructionsWidget,
    GreetDeviceCodeExchangeWidget,
    GreetDeviceWidget,
)

from tests.common import customize_fixtures


@pytest.fixture
def catch_greet_device_widget(widget_catcher_factory):
    return widget_catcher_factory(
        "parsec.core.gui.greet_device_widget.GreetDeviceInstructionsWidget",
        "parsec.core.gui.greet_device_widget.GreetDeviceCodeExchangeWidget",
        "parsec.core.gui.greet_device_widget.GreetDeviceWidget",
    )


@pytest.mark.gui
@pytest.mark.trio
async def test_invite_device_offline(aqtbot, logged_gui, autoclose_dialog, running_backend):
    d_w = await logged_gui.test_switch_to_devices_widget()

    # TODO: not sure why but this timeout without running_backend fixture...
    with running_backend.offline():
        await aqtbot.mouse_click(d_w.button_add_device, QtCore.Qt.LeftButton)

        def _invite_failed():
            assert autoclose_dialog.dialogs == [
                ("Error", "The server is offline or you have no access to the internet.")
            ]

        await aqtbot.wait_until(_invite_failed)


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize("online", (True, False))
async def test_invite_device_send_email(
    aqtbot,
    logged_gui,
    running_backend,
    autoclose_dialog,
    email_letterbox,
    catch_greet_device_widget,
    bob,
    online,
):
    d_w = await logged_gui.test_switch_to_devices_widget()

    await aqtbot.mouse_click(d_w.button_add_device, QtCore.Qt.LeftButton)

    # Device invitation widget should show up now

    gd_w = await catch_greet_device_widget()
    assert isinstance(gd_w, GreetDeviceWidget)

    gdi_w = await catch_greet_device_widget()
    assert isinstance(gdi_w, GreetDeviceInstructionsWidget)

    def _greet_device_displayed():
        assert gd_w.dialog.isVisible()
        assert gd_w.isVisible()
        assert gd_w.dialog.label_title.text() == "Greet a new device"
        assert gdi_w.isVisible()
        assert gdi_w.button_send_email.isVisible()
        assert gdi_w.button_copy_addr.isVisible()

    await aqtbot.wait_until(_greet_device_displayed)

    if online:
        await aqtbot.mouse_click(gdi_w.button_send_email, QtCore.Qt.LeftButton)

        def _email_sent():
            assert email_letterbox.emails == [(bob.human_handle.email, ANY)]

        await aqtbot.wait_until(_email_sent)
        assert not autoclose_dialog.dialogs

    else:
        with running_backend.offline():
            await aqtbot.mouse_click(gdi_w.button_send_email, QtCore.Qt.LeftButton)

            def _email_send_failed():
                assert autoclose_dialog.dialogs == [("Error", "Could not send the email.")]

            await aqtbot.wait_until(_email_send_failed)
            assert not email_letterbox.emails


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(bob_has_human_handle=False)
async def test_invite_device_without_human_handle_cannot_send_email(
    aqtbot, logged_gui, running_backend, autoclose_dialog, catch_greet_device_widget
):
    d_w = await logged_gui.test_switch_to_devices_widget()

    await aqtbot.mouse_click(d_w.button_add_device, QtCore.Qt.LeftButton)

    # Device invitation widget should show up now

    gd_w = await catch_greet_device_widget()
    assert isinstance(gd_w, GreetDeviceWidget)

    gdi_w = await catch_greet_device_widget()
    assert isinstance(gdi_w, GreetDeviceInstructionsWidget)

    def _greet_device_displayed():
        assert gd_w.dialog.isVisible()
        assert gd_w.isVisible()
        assert gd_w.dialog.label_title.text() == "Greet a new device"
        assert gdi_w.isVisible()
        assert not gdi_w.button_send_email.isVisible()
        assert gdi_w.button_copy_addr.isVisible()

    await aqtbot.wait_until(_greet_device_displayed)
    assert not autoclose_dialog.dialogs


# TODO: test copy invitation link


@pytest.mark.gui
@pytest.mark.trio
async def test_invite_and_greet_device(
    aqtbot, logged_gui, running_backend, autoclose_dialog, catch_greet_device_widget, bob
):
    requested_device_label = "PC1"

    # First switch to devices page, and click on "new device" button

    d_w = await logged_gui.test_switch_to_devices_widget()

    await aqtbot.mouse_click(d_w.button_add_device, QtCore.Qt.LeftButton)

    # Device invitation widget should show up now with welcome page

    gd_w = await catch_greet_device_widget()
    assert isinstance(gd_w, GreetDeviceWidget)

    gdi_w = await catch_greet_device_widget()
    assert isinstance(gdi_w, GreetDeviceInstructionsWidget)

    def _greet_device_displayed():
        assert gd_w.dialog.isVisible()
        assert gd_w.isVisible()
        assert gd_w.dialog.label_title.text() == "Greet a new device"
        assert gdi_w.isVisible()

    await aqtbot.wait_until(_greet_device_displayed)

    # Now we can setup the boilerplates for the test

    start_claimer = trio.Event()
    start_claimer_trust = trio.Event()
    start_claimer_claim_user = trio.Event()

    greeter_sas = None
    greeter_sas_available = trio.Event()
    claimer_sas = None
    claimer_sas_available = trio.Event()
    claimer_done = trio.Event()

    async def _run_claimer():
        nonlocal greeter_sas
        nonlocal claimer_sas

        async with backend_invited_cmds_factory(addr=gdi_w.invite_addr) as cmds:
            await start_claimer.wait()

            initial_ctx = await claimer_retrieve_info(cmds)
            in_progress_ctx = await initial_ctx.do_wait_peer()
            greeter_sas = in_progress_ctx.greeter_sas
            greeter_sas_available.set()

            await start_claimer_trust.wait()

            in_progress_ctx = await in_progress_ctx.do_signify_trust()
            claimer_sas = in_progress_ctx.claimer_sas
            claimer_sas_available.set()
            in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()

            await start_claimer_claim_user.wait()

            await in_progress_ctx.do_claim_device(requested_device_label=requested_device_label)
            claimer_done.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(_run_claimer)

        # Start the greeting

        await aqtbot.mouse_click(gdi_w.button_start, QtCore.Qt.LeftButton)

        def _greet_started():
            assert not gdi_w.button_start.isEnabled()
            assert gdi_w.button_start.text() == "Waiting for the new device..."

        await aqtbot.wait_until(_greet_started)

        # Start the claimer, this should change page to code exchange
        start_claimer.set()

        gdce_w = await catch_greet_device_widget()
        assert isinstance(gdce_w, GreetDeviceCodeExchangeWidget)
        await greeter_sas_available.wait()

        def _greeter_code_displayed():
            assert not gdi_w.isVisible()
            assert gdce_w.isVisible()
            # We should be displaying the greeter SAS code
            assert not gdce_w.label_wait_info.isVisible()
            assert gdce_w.widget_greeter_code.isVisible()
            assert not gdce_w.widget_claimer_code.isVisible()
            assert not gdce_w.code_input_widget.isVisible()
            assert gdce_w.line_edit_greeter_code.text() == greeter_sas

        await aqtbot.wait_until(_greeter_code_displayed)

        # Pretent the code was correctly transmitted to the claimer
        start_claimer_trust.set()

        def _claimer_code_choices_displayed():
            assert not gdce_w.label_wait_info.isVisible()
            assert not gdce_w.widget_greeter_code.isVisible()
            assert gdce_w.widget_claimer_code.isVisible()
            assert gdce_w.code_input_widget.isVisible()
            assert gdce_w.code_input_widget.code_layout.count() == 4
            # TODO: better check on codes

        await aqtbot.wait_until(_claimer_code_choices_displayed)

        # Pretend we have choosen the right code
        # TODO: click on button instead of sending the corresponding event
        gdce_w.code_input_widget.good_code_clicked.emit()

        def _wait_claimer_info():
            assert gdce_w.label_wait_info.isVisible()
            assert not gdce_w.widget_greeter_code.isVisible()
            assert not gdce_w.widget_claimer_code.isVisible()

        await aqtbot.wait_until(_wait_claimer_info)

        # Finally claimer info arrive and we finish the greeting !
        start_claimer_claim_user.set()

        def _greet_done():
            assert not gd_w.isVisible()
            assert autoclose_dialog.dialogs == [("", "The device was successfully created.")]
            # Devices list should be updated
            assert d_w.layout_devices.count() == 2
            item = d_w.layout_devices.itemAt(1)
            assert item.widget().label_device_name.text() == requested_device_label
            assert item.widget().label_is_current.text() == ""

        await aqtbot.wait_until(_greet_done)

        with trio.fail_after(1):
            await claimer_done.wait()


# TODO: test offline during the claim steps
# TODO: test other-than offline errors (e.g. invitation used by another client in our back)
