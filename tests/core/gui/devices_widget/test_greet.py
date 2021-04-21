# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from PyQt5 import QtCore
from functools import partial
from async_generator import asynccontextmanager
from parsec.datetime import now as datetime_now

from parsec.utils import start_task
from parsec.core.gui.lang import translate
from parsec.api.protocol import InvitationType, HumanHandle, InvitationDeletedReason
from parsec.core.types import BackendInvitationAddr
from parsec.core.backend_connection import backend_invited_cmds_factory
from parsec.core.invite import claimer_retrieve_info
from parsec.core.gui.greet_device_widget import (
    GreetDeviceCodeExchangeWidget,
    GreetDeviceInstructionsWidget,
    GreetDeviceWidget,
)
from parsec.core.gui.devices_widget import DeviceButton

from tests.common import customize_fixtures


@pytest.fixture
def catch_greet_device_widget(widget_catcher_factory):
    return widget_catcher_factory(
        "parsec.core.gui.greet_device_widget.GreetDeviceCodeExchangeWidget",
        "parsec.core.gui.greet_device_widget.GreetDeviceInstructionsWidget",
        "parsec.core.gui.greet_device_widget.GreetDeviceWidget",
    )


@pytest.fixture
def GreetDeviceTestBed(
    aqtbot, catch_greet_device_widget, autoclose_dialog, backend, running_backend, logged_gui
):
    class _GreetDeviceTestBed:
        def __init__(self):
            self.requested_human_handle = HumanHandle(
                email="brod@pe.com", label="Bender B. Rodriguez"
            )
            self.requested_device_label = "PC1"
            self.steps_done = []

            # Set during bootstrap
            self.author = None
            self.devices_widget = None
            self.invitation_addr = None
            self.greet_device_widget = None
            self.greet_device_information_widget = None
            self.cmds = None

            # Set by step 2
            self.greet_device_code_exchange_widget = None

            # Set by step 5
            self.claimer_claim_task = None

        async def run(self):
            await self.bootstrap()
            async with trio.open_nursery() as self.nursery:
                async with backend_invited_cmds_factory(addr=self.invitation_addr) as self.cmds:
                    next_step = "step_1_start_greet"
                    while True:
                        current_step = next_step
                        next_step = await getattr(self, current_step)()
                        self.steps_done.append(current_step)
                        if next_step is None:
                            break
                    if self.claimer_claim_task:
                        await self.claimer_claim_task.cancel_and_join()

        async def bootstrap(self):
            author = logged_gui.test_get_central_widget().core.device
            # Create new invitation

            invitation = await backend.invite.new_for_device(
                organization_id=author.organization_id, greeter_user_id=author.user_id
            )
            invitation_addr = BackendInvitationAddr.build(
                backend_addr=author.organization_addr,
                organization_id=author.organization_id,
                invitation_type=InvitationType.DEVICE,
                token=invitation.token,
            )

            # Switch to devices page
            devices_widget = await logged_gui.test_switch_to_devices_widget()
            assert devices_widget.layout_devices.count() == 2

            # Click on the invitation button
            await aqtbot.mouse_click(devices_widget.button_add_device, QtCore.Qt.LeftButton)
            greet_device_widget = await catch_greet_device_widget()
            assert isinstance(greet_device_widget, GreetDeviceWidget)

            greet_device_information_widget = await catch_greet_device_widget()
            assert isinstance(greet_device_information_widget, GreetDeviceInstructionsWidget)

            def _greet_device_displayed():
                assert greet_device_widget.dialog.isVisible()
                assert greet_device_widget.isVisible()
                assert greet_device_widget.dialog.label_title.text() == "Greet a new device"
                assert greet_device_information_widget.isVisible()

            await aqtbot.wait_until(_greet_device_displayed)

            self.author = author
            self.devices_widget = devices_widget
            self.invitation_addr = invitation_addr
            self.greet_device_widget = greet_device_widget
            self.greet_device_information_widget = greet_device_information_widget

            self.assert_initial_state()  # Sanity check

        async def bootstrap_after_restart(self):
            self.greet_device_information_widget = None
            self.greet_device_code_exchange_widget = None

            greet_device_widget = self.greet_device_widget
            greet_device_information_widget = await catch_greet_device_widget()
            assert isinstance(greet_device_information_widget, GreetDeviceInstructionsWidget)

            def _greet_device_displayed():
                assert greet_device_widget.dialog.isVisible()
                assert greet_device_widget.isVisible()
                assert greet_device_widget.dialog.label_title.text() == "Greet a new device"
                assert greet_device_information_widget.isVisible()

            await aqtbot.wait_until(_greet_device_displayed)

            self.greet_device_widget = greet_device_widget
            self.greet_device_information_widget = greet_device_information_widget

            self.assert_initial_state()  # Sanity check

        def assert_initial_state(self):
            assert self.greet_device_widget.isVisible()
            assert self.greet_device_information_widget.isVisible()
            assert self.greet_device_information_widget.button_start.isEnabled()
            if self.greet_device_code_exchange_widget:
                assert not self.greet_device_code_exchange_widget.isVisible()

        async def step_1_start_greet(self):
            gdi_w = self.greet_device_information_widget

            await aqtbot.mouse_click(gdi_w.button_start, QtCore.Qt.LeftButton)

            def _greet_started():
                assert not gdi_w.button_start.isEnabled()
                assert gdi_w.button_start.text() == "Waiting for the new device..."

            await aqtbot.wait_until(_greet_started)
            return "step_2_start_claimer"

        async def step_2_start_claimer(self):
            gdi_w = self.greet_device_information_widget

            self.claimer_initial_ctx = await claimer_retrieve_info(self.cmds)
            self.claimer_in_progress_ctx = await self.claimer_initial_ctx.do_wait_peer()
            greeter_sas = self.claimer_in_progress_ctx.greeter_sas

            gdce_w = await catch_greet_device_widget()
            assert isinstance(gdce_w, GreetDeviceCodeExchangeWidget)

            def _greeter_sas_displayed():
                assert not gdi_w.isVisible()
                assert gdce_w.isVisible()
                # We should be displaying the greeter SAS code
                assert gdce_w.widget_greeter_code.isVisible()
                assert not gdce_w.widget_claimer_code.isVisible()
                assert not gdce_w.code_input_widget.isVisible()
                assert gdce_w.line_edit_greeter_code.text() == greeter_sas

            await aqtbot.wait_until(_greeter_sas_displayed)

            self.greet_device_code_exchange_widget = gdce_w

            return "step_3_exchange_greeter_sas"

        async def step_3_exchange_greeter_sas(self):
            gdce_w = self.greet_device_code_exchange_widget

            self.claimer_in_progress_ctx = await self.claimer_in_progress_ctx.do_signify_trust()
            self.claimer_sas = self.claimer_in_progress_ctx.claimer_sas

            def _claimer_code_choices_displayed():
                assert not gdce_w.widget_greeter_code.isVisible()
                assert gdce_w.widget_claimer_code.isVisible()
                assert gdce_w.code_input_widget.isVisible()
                assert gdce_w.code_input_widget.code_layout.count() == 4

            await aqtbot.wait_until(_claimer_code_choices_displayed)

            return "step_4_exchange_claimer_sas"

        async def step_4_exchange_claimer_sas(self):
            gdce_w = self.greet_device_code_exchange_widget

            # Pretent we have clicked on the right choice
            await aqtbot.run(gdce_w.code_input_widget.good_code_clicked.emit)

            self.claimer_in_progress_ctx = await self.claimer_in_progress_ctx.do_wait_peer_trust()

            def _wait_claimer_info():
                assert not gdce_w.widget_greeter_code.isVisible()
                assert not gdce_w.widget_claimer_code.isVisible()
                assert gdce_w.isVisible()

            await aqtbot.wait_until(_wait_claimer_info)
            return "step_5_provide_claim_info"

        async def step_5_provide_claim_info(self):
            gdce_w = self.greet_device_code_exchange_widget

            async def _claimer_claim(in_progress_ctx, task_status=trio.TASK_STATUS_IGNORED):
                task_status.started()
                await in_progress_ctx.do_claim_device(
                    requested_device_label=self.requested_device_label
                )

            self.claimer_claim_task = await start_task(
                self.nursery, _claimer_claim, self.claimer_in_progress_ctx
            )
            with trio.fail_after(1):
                await self.claimer_claim_task.join()

            def _greet_done():
                assert not gdce_w.isVisible()
                assert autoclose_dialog.dialogs == [("", "The device was successfully created.")]
                assert self.devices_widget.layout_devices.count() == 3
                device_button = self.devices_widget.layout_devices.itemAt(2).widget()
                assert isinstance(device_button, DeviceButton)
                assert device_button.device_info.device_label == "PC1"
                assert device_button.label_device_name.text() == "PC1"

            await aqtbot.wait_until(_greet_done)
            return

    return _GreetDeviceTestBed


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_device(GreetDeviceTestBed):
    await GreetDeviceTestBed().run()


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize(
    "offline_step",
    [
        "step_1_start_greet",
        "step_2_start_claimer",
        "step_3_exchange_greeter_sas",
        "step_4_exchange_claimer_sas",
        "step_5_provide_claim_info",
    ],
)
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_device_offline(
    aqtbot, GreetDeviceTestBed, running_backend, autoclose_dialog, offline_step
):
    class OfflineTestBed(GreetDeviceTestBed):
        def _greet_aborted(self, expected_message):
            assert len(autoclose_dialog.dialogs) == 1
            assert autoclose_dialog.dialogs == [("Error", expected_message)]
            assert not self.greet_device_widget.isVisible()
            assert not self.greet_device_information_widget.isVisible()

        async def offline_step_1_start_greet(self):
            expected_message = translate("TEXT_GREET_DEVICE_WAIT_PEER_ERROR")
            gui_w = self.greet_device_information_widget

            with running_backend.offline():
                await aqtbot.mouse_click(gui_w.button_start, QtCore.Qt.LeftButton)
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_2_start_claimer(self):
            expected_message = translate("TEXT_GREET_DEVICE_WAIT_PEER_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_3_exchange_greeter_sas(self):
            expected_message = translate("TEXT_GREET_DEVICE_WAIT_PEER_TRUST_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_GREET_DEVICE_SIGNIFY_TRUST_ERROR")
            guce_w = self.greet_device_code_exchange_widget

            with running_backend.offline():
                await aqtbot.run(guce_w.code_input_widget.good_code_clicked.emit)
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_GREET_DEVICE_SIGNIFY_TRUST_ERROR")

            with running_backend.offline():
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

    setattr(OfflineTestBed, offline_step, getattr(OfflineTestBed, f"offline_{offline_step}"))

    await OfflineTestBed().run()


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize(
    "reset_step",
    ["step_3_exchange_greeter_sas", "step_4_exchange_claimer_sas", "step_5_provide_claim_info"],
)
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_device_reset_by_peer(aqtbot, GreetDeviceTestBed, autoclose_dialog, reset_step):
    class ResetTestBed(GreetDeviceTestBed):
        @asynccontextmanager
        async def _reset_claimer(self):
            async with backend_invited_cmds_factory(addr=self.invitation_addr) as cmds:
                claimer_initial_ctx = await claimer_retrieve_info(cmds)
                async with trio.open_nursery() as nursery:
                    nursery.start_soon(claimer_initial_ctx.do_wait_peer)
                    yield
                    nursery.cancel_scope.cancel()

        def _greet_restart(self, expected_message):
            assert autoclose_dialog.dialogs == [("Error", expected_message)]

        # Step 1&2 are before peer wait, so reset is meaningless

        async def reset_step_3_exchange_greeter_sas(self):
            expected_message = translate("TEXT_GREET_DEVICE_PEER_RESET")
            async with self._reset_claimer():
                await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_GREET_DEVICE_PEER_RESET")
            guce_w = self.greet_device_code_exchange_widget

            # Pretent we have click on the right choice
            await aqtbot.run(guce_w.code_input_widget.good_code_clicked.emit)

            async with self._reset_claimer():
                await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_GREET_DEVICE_PEER_RESET")

            async with self._reset_claimer():
                await aqtbot.wait_until(partial(self._greet_restart, expected_message))
            await self.bootstrap_after_restart()

    setattr(ResetTestBed, reset_step, getattr(ResetTestBed, f"reset_{reset_step}"))

    await ResetTestBed().run()


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize(
    "cancelled_step",
    [
        "step_1_start_greet",
        pytest.param("step_2_start_claimer", marks=pytest.mark.xfail),
        pytest.param("step_3_exchange_greeter_sas", marks=pytest.mark.xfail),
        "step_4_exchange_claimer_sas",
        pytest.param("step_5_provide_claim_info", marks=pytest.mark.xfail),
    ],
)
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_device_invitation_cancelled(
    aqtbot, GreetDeviceTestBed, backend, autoclose_dialog, cancelled_step
):
    class CancelledTestBed(GreetDeviceTestBed):
        async def _cancel_invitation(self):
            await backend.invite.delete(
                organization_id=self.author.organization_id,
                greeter=self.author.user_id,
                token=self.invitation_addr.token,
                on=datetime_now(),
                reason=InvitationDeletedReason.CANCELLED,
            )

        def _greet_restart(self, expected_message):
            assert len(autoclose_dialog.dialogs) == 1
            assert autoclose_dialog.dialogs == [("Error", expected_message)]
            assert not self.greet_device_widget.isVisible()
            assert not self.greet_device_information_widget.isVisible()

        async def cancelled_step_1_start_greet(self):
            expected_message = translate("TEXT_GREET_DEVICE_WAIT_PEER_ERROR")
            gui_w = self.greet_device_information_widget

            await self._cancel_invitation()

            await aqtbot.mouse_click(gui_w.button_start, QtCore.Qt.LeftButton)
            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_2_start_claimer(self):
            expected_message = translate("TEXT_GREET_DEVICE_WAIT_PEER_ERROR")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_3_exchange_greeter_sas(self):
            expected_message = translate("TEXT_GREET_DEVICE_WAIT_PEER_TRUST_ERROR")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_GREET_DEVICE_SIGNIFY_TRUST_ERROR")
            guce_w = self.greet_device_code_exchange_widget

            await self._cancel_invitation()

            await aqtbot.run(guce_w.code_input_widget.good_code_clicked.emit)
            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_GREET_DEVICE_WAIT_PEER_TRUST_ERROR")

            await self._cancel_invitation()
            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

    setattr(
        CancelledTestBed, cancelled_step, getattr(CancelledTestBed, f"cancelled_{cancelled_step}")
    )

    await CancelledTestBed().run()
