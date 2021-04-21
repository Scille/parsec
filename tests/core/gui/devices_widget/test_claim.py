# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from parsec.datetime import now as datetime_now
from PyQt5 import QtCore
from async_generator import asynccontextmanager
from functools import partial

from uuid import uuid4

from parsec.api.protocol import InvitationType, InvitationDeletedReason
from parsec.core.types import BackendInvitationAddr
from parsec.core.invite import DeviceGreetInitialCtx
from parsec.core.gui.lang import translate
from parsec.core.gui.claim_device_widget import (
    ClaimDeviceCodeExchangeWidget,
    ClaimDeviceProvideInfoWidget,
    ClaimDeviceInstructionsWidget,
    ClaimDeviceWidget,
)


@pytest.fixture
def catch_claim_device_widget(widget_catcher_factory):
    return widget_catcher_factory(
        "parsec.core.gui.claim_device_widget.ClaimDeviceCodeExchangeWidget",
        "parsec.core.gui.claim_device_widget.ClaimDeviceProvideInfoWidget",
        "parsec.core.gui.claim_device_widget.ClaimDeviceInstructionsWidget",
        "parsec.core.gui.claim_device_widget.ClaimDeviceWidget",
    )


@pytest.fixture
def ClaimDeviceTestBed(
    aqtbot,
    catch_claim_device_widget,
    autoclose_dialog,
    backend,
    running_backend,
    gui,
    alice,
    alice_backend_cmds,
):
    class _ClaimDeviceTestBed:
        def __init__(self):
            self.requested_device_label = "PC1"
            self.password = "P@ssw0rd."
            self.steps_done = []

            self.author = alice
            self.cmds = alice_backend_cmds

            # Set during bootstrap
            self.invitation_addr = None
            self.claim_device_widget = None
            self.claim_device_instructions_widget = None

            # Set by step 2
            self.claim_device_code_exchange_widget = None

            # Set by step 4
            self.claim_device_provide_info_widget = None

        async def run(self):
            await self.bootstrap()
            async with trio.open_nursery() as self.nursery:
                next_step = "step_1_start_claim"
                while True:
                    current_step = next_step
                    next_step = await getattr(self, current_step)()
                    self.steps_done.append(current_step)
                    if next_step is None:
                        break

        async def bootstrap(self):
            # Create new invitation

            invitation = await backend.invite.new_for_device(
                organization_id=self.author.organization_id, greeter_user_id=self.author.user_id
            )
            invitation_addr = BackendInvitationAddr.build(
                backend_addr=self.author.organization_addr,
                organization_id=self.author.organization_id,
                invitation_type=InvitationType.DEVICE,
                token=invitation.token,
            )

            # Switch to device claim page

            await aqtbot.run(gui.add_instance, invitation_addr.to_url())

            cd_w = await catch_claim_device_widget()
            assert isinstance(cd_w, ClaimDeviceWidget)
            cdi_w = await catch_claim_device_widget()
            assert isinstance(cdi_w, ClaimDeviceInstructionsWidget)

            def _register_device_displayed():
                tab = gui.test_get_tab()
                assert tab and tab.isVisible()
                assert cd_w.isVisible()
                assert cd_w.dialog.label_title.text() == "Register a device"
                assert cdi_w.isVisible()

            await aqtbot.wait_until(_register_device_displayed)

            self.invitation_addr = invitation_addr
            self.claim_device_widget = cd_w
            self.claim_device_instructions_widget = cdi_w

            self.assert_initial_state()  # Sanity check

        async def bootstrap_after_restart(self):
            self.claim_device_instructions_widget = None
            self.claim_device_code_exchange_widget = None
            self.claim_device_provide_info_widget = None

            cd_w = self.claim_device_widget
            cdi_w = await catch_claim_device_widget()
            assert isinstance(cdi_w, ClaimDeviceInstructionsWidget)

            def _register_device_displayed():
                tab = gui.test_get_tab()
                assert tab and tab.isVisible()
                assert cd_w.isVisible()
                assert cd_w.dialog.label_title.text() == "Register a device"
                assert cdi_w.isVisible()

            await aqtbot.wait_until(_register_device_displayed)

            self.claim_device_widget = cd_w
            self.claim_device_instructions_widget = cdi_w

            self.assert_initial_state()  # Sanity check

        def assert_initial_state(self):
            assert self.claim_device_widget.isVisible()
            assert self.claim_device_instructions_widget.isVisible()
            assert self.claim_device_instructions_widget.button_start.isEnabled()
            if self.claim_device_code_exchange_widget:
                assert not self.claim_device_code_exchange_widget.isVisible()
            if self.claim_device_provide_info_widget:
                assert not self.claim_device_provide_info_widget.isVisible()

        async def step_1_start_claim(self):
            cdi_w = self.claim_device_instructions_widget
            await aqtbot.mouse_click(cdi_w.button_start, QtCore.Qt.LeftButton)

            def _claimer_started():
                assert not cdi_w.button_start.isEnabled()
                assert cdi_w.button_start.text() == "Waiting..."

            await aqtbot.wait_until(_claimer_started)

            return "step_2_start_greeter"

        async def step_2_start_greeter(self):
            cdi_w = self.claim_device_instructions_widget

            self.greeter_initial_ctx = DeviceGreetInitialCtx(
                cmds=self.cmds, token=self.invitation_addr.token
            )
            self.greeter_in_progress_ctx = await self.greeter_initial_ctx.do_wait_peer()

            cdce_w = await catch_claim_device_widget()
            assert isinstance(cdce_w, ClaimDeviceCodeExchangeWidget)

            def _greeter_sas_code_choices_displayed():
                assert not cdi_w.isVisible()
                assert cdce_w.isVisible()
                assert cdce_w.widget_greeter_code.isVisible()
                assert cdce_w.code_input_widget.isVisible()
                assert cdce_w.code_input_widget.code_layout.count() == 4
                # TODO: better check on codes

            await aqtbot.wait_until(_greeter_sas_code_choices_displayed)

            self.claim_device_code_exchange_widget = cdce_w

            return "step_3_exchange_greeter_sas"

        async def step_3_exchange_greeter_sas(self):
            cdce_w = self.claim_device_code_exchange_widget

            # Pretend we have choosen the right code
            await aqtbot.run(cdce_w.code_input_widget.good_code_clicked.emit)

            self.greeter_in_progress_ctx = await self.greeter_in_progress_ctx.do_wait_peer_trust()
            claimer_sas = self.greeter_in_progress_ctx.claimer_sas

            def _claimer_sas_code_displayed():
                assert not cdce_w.widget_greeter_code.isVisible()
                assert not cdce_w.code_input_widget.isVisible()
                assert cdce_w.widget_claimer_code.isVisible()
                assert cdce_w.line_edit_claimer_code.isVisible()
                assert cdce_w.line_edit_claimer_code.text() == claimer_sas

            await aqtbot.wait_until(_claimer_sas_code_displayed)

            return "step_4_exchange_claimer_sas"

        async def step_4_exchange_claimer_sas(self):
            cdce_w = self.claim_device_code_exchange_widget

            self.greeter_in_progress_ctx = await self.greeter_in_progress_ctx.do_signify_trust()

            cdpi_w = await catch_claim_device_widget()
            assert isinstance(cdpi_w, ClaimDeviceProvideInfoWidget)

            def _claim_info_displayed():
                assert not cdce_w.isVisible()
                assert cdpi_w.isVisible()
                assert cdpi_w.line_edit_device.text()  # Should have a default value

            await aqtbot.wait_until(_claim_info_displayed)

            self.claim_device_provide_info_widget = cdpi_w

            return "step_5_provide_claim_info"

        async def step_5_provide_claim_info(self):
            cdpi_w = self.claim_device_provide_info_widget
            device_label = self.requested_device_label

            await aqtbot.run(cdpi_w.line_edit_device.clear)

            assert not cdpi_w.button_ok.isEnabled()

            await aqtbot.key_clicks(cdpi_w.line_edit_device, device_label)
            await aqtbot.key_clicks(cdpi_w.widget_password.line_edit_password, self.password)
            await aqtbot.key_clicks(cdpi_w.widget_password.line_edit_password_check, self.password)

            assert cdpi_w.button_ok.isEnabled()

            await aqtbot.mouse_click(cdpi_w.button_ok, QtCore.Qt.LeftButton)

            def _claim_info_submitted():
                assert not cdpi_w.button_ok.isEnabled()
                assert cdpi_w.label_wait.isVisible()

            await aqtbot.wait_until(_claim_info_submitted)

            self.greeter_in_progress_ctx = (
                await self.greeter_in_progress_ctx.do_get_claim_requests()
            )
            assert (
                self.greeter_in_progress_ctx.requested_device_label == self.requested_device_label
            )

            return "step_6_validate_claim_info"

        async def step_6_validate_claim_info(self):
            cd_w = self.claim_device_widget
            cdpi_w = self.claim_device_provide_info_widget

            await self.greeter_in_progress_ctx.do_create_new_device(
                author=self.author, device_label=self.greeter_in_progress_ctx.requested_device_label
            )

            def _claim_done():
                assert not cd_w.isVisible()
                assert not cdpi_w.isVisible()
                # Should be logged in with the new device
                central_widget = gui.test_get_central_widget()
                assert central_widget and central_widget.isVisible()
                assert autoclose_dialog.dialogs == [("", "The device was successfully created!")]

            await aqtbot.wait_until(_claim_done)

            return None  # Test is done \o/

    return _ClaimDeviceTestBed


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device(ClaimDeviceTestBed):
    await ClaimDeviceTestBed().run()


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize(
    "offline_step",
    [
        "step_1_start_claim",
        "step_2_start_greeter",
        "step_3_exchange_greeter_sas",
        "step_4_exchange_claimer_sas",
        "step_5_provide_claim_info",
        "step_6_validate_claim_info",
    ],
)
async def test_claim_device_offline(
    aqtbot, ClaimDeviceTestBed, running_backend, autoclose_dialog, offline_step
):
    class OfflineTestBed(ClaimDeviceTestBed):
        def _claim_aborted(self, expected_message):
            assert len(autoclose_dialog.dialogs) == 1
            assert autoclose_dialog.dialogs == [("Error", expected_message)]
            assert not self.claim_device_widget.isVisible()
            assert not self.claim_device_instructions_widget.isVisible()

        async def offline_step_1_start_claim(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_WAIT_PEER_ERROR")
            cdi_w = self.claim_device_instructions_widget

            with running_backend.offline():
                await aqtbot.mouse_click(cdi_w.button_start, QtCore.Qt.LeftButton)
                await aqtbot.wait_until(partial(self._claim_aborted, expected_message))

            return None

        async def offline_step_2_start_greeter(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_WAIT_PEER_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._claim_aborted, expected_message))

            return None

        async def offline_step_3_exchange_greeter_sas(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_SIGNIFY_TRUST_ERROR")
            cdce_w = self.claim_device_code_exchange_widget

            with running_backend.offline():
                assert not autoclose_dialog.dialogs
                await aqtbot.run(cdce_w.code_input_widget.good_code_clicked.emit)
                await aqtbot.wait_until(partial(self._claim_aborted, expected_message))
            return None

        async def offline_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_WAIT_PEER_TRUST_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._claim_aborted, expected_message))

            return None

        async def offline_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_CLAIM_ERROR")
            cdpi_w = self.claim_device_provide_info_widget
            device_label = self.requested_device_label

            with running_backend.offline():
                await aqtbot.run(cdpi_w.line_edit_device.clear)
                await aqtbot.key_clicks(cdpi_w.line_edit_device, device_label)
                await aqtbot.key_clicks(cdpi_w.widget_password.line_edit_password, self.password)
                await aqtbot.key_clicks(
                    cdpi_w.widget_password.line_edit_password_check, self.password
                )
                await aqtbot.mouse_click(cdpi_w.button_ok, QtCore.Qt.LeftButton)
                await aqtbot.wait_until(partial(self._claim_aborted, expected_message))

            return None

        async def offline_step_6_validate_claim_info(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_CLAIM_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._claim_aborted, expected_message))

            return None

    setattr(OfflineTestBed, offline_step, getattr(OfflineTestBed, f"offline_{offline_step}"))

    await OfflineTestBed().run()


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize(
    "reset_step",
    [
        "step_3_exchange_greeter_sas",
        "step_4_exchange_claimer_sas",
        "step_5_provide_claim_info",
        "step_6_validate_claim_info",
    ],
)
async def test_claim_device_reset_by_peer(
    aqtbot, ClaimDeviceTestBed, running_backend, autoclose_dialog, reset_step, alice2_backend_cmds
):
    class ResetTestBed(ClaimDeviceTestBed):
        @asynccontextmanager
        async def _reset_greeter(self):
            async with trio.open_nursery() as nursery:
                greeter_initial_ctx = DeviceGreetInitialCtx(
                    cmds=alice2_backend_cmds, token=self.invitation_addr.token
                )
                nursery.start_soon(greeter_initial_ctx.do_wait_peer)
                yield
                nursery.cancel_scope.cancel()

        def _claim_restart(self, expected_message):
            assert len(autoclose_dialog.dialogs) == 1
            assert autoclose_dialog.dialogs == [("Error", expected_message)]

        # Step 1&2 are before peer wait, so reset is meaningless

        async def reset_step_3_exchange_greeter_sas(self):
            cdce_w = self.claim_device_code_exchange_widget
            expected_message = translate("TEXT_CLAIM_DEVICE_PEER_RESET")

            async with self._reset_greeter():
                await aqtbot.run(cdce_w.code_input_widget.good_code_clicked.emit)
                await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_WAIT_PEER_TRUST_ERROR")
            async with self._reset_greeter():
                await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_PEER_RESET")
            cdpi_w = self.claim_device_provide_info_widget
            device_label = self.requested_device_label

            async with self._reset_greeter():
                await aqtbot.run(cdpi_w.line_edit_device.clear)
                await aqtbot.key_clicks(cdpi_w.line_edit_device, device_label)
                await aqtbot.key_clicks(cdpi_w.widget_password.line_edit_password, self.password)
                await aqtbot.key_clicks(
                    cdpi_w.widget_password.line_edit_password_check, self.password
                )
                await aqtbot.mouse_click(cdpi_w.button_ok, QtCore.Qt.LeftButton)
                await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_6_validate_claim_info(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_PEER_RESET")
            async with self._reset_greeter():
                await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

    setattr(ResetTestBed, reset_step, getattr(ResetTestBed, f"reset_{reset_step}"))

    await ResetTestBed().run()


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize(
    "cancelled_step",
    [
        "step_1_start_claim",
        "step_2_start_greeter",
        "step_3_exchange_greeter_sas",
        "step_4_exchange_claimer_sas",
        "step_5_provide_claim_info",
        "step_6_validate_claim_info",
    ],
)
async def test_claim_device_invitation_cancelled(
    aqtbot, ClaimDeviceTestBed, running_backend, backend, autoclose_dialog, cancelled_step
):
    class CancelledTestBed(ClaimDeviceTestBed):
        async def _cancel_invitation(self):
            await backend.invite.delete(
                organization_id=self.author.organization_id,
                greeter=self.author.user_id,
                token=self.invitation_addr.token,
                on=datetime_now(),
                reason=InvitationDeletedReason.CANCELLED,
            )

        def _claim_restart(self, expected_message):
            assert len(autoclose_dialog.dialogs) == 1
            assert autoclose_dialog.dialogs == [("Error", expected_message)]
            assert not self.claim_device_widget.isVisible()
            assert not self.claim_device_instructions_widget.isVisible()

        async def cancelled_step_1_start_claim(self):
            expected_message = translate("TEXT_INVITATION_ALREADY_USED")
            cdi_w = self.claim_device_instructions_widget

            await self._cancel_invitation()

            await aqtbot.mouse_click(cdi_w.button_start, QtCore.Qt.LeftButton)
            await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            return None

        async def cancelled_step_2_start_greeter(self):
            expected_message = translate("TEXT_INVITATION_ALREADY_USED")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            return None

        async def cancelled_step_3_exchange_greeter_sas(self):
            expected_message = translate("TEXT_INVITATION_ALREADY_USED")
            cdce_w = self.claim_device_code_exchange_widget
            await self._cancel_invitation()

            await aqtbot.run(cdce_w.code_input_widget.good_code_clicked.emit)
            await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            return None

        async def cancelled_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_CLAIM_DEVICE_WAIT_PEER_TRUST_ERROR")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            return None

        async def cancelled_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_INVITATION_ALREADY_USED")
            cdpi_w = self.claim_device_provide_info_widget
            device_label = self.requested_device_label

            await self._cancel_invitation()

            await aqtbot.run(cdpi_w.line_edit_device.clear)
            await aqtbot.key_clicks(cdpi_w.line_edit_device, device_label)
            await aqtbot.key_clicks(cdpi_w.widget_password.line_edit_password, self.password)
            await aqtbot.key_clicks(cdpi_w.widget_password.line_edit_password_check, self.password)
            await aqtbot.mouse_click(cdpi_w.button_ok, QtCore.Qt.LeftButton)
            await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            return None

        async def cancelled_step_6_validate_claim_info(self):
            expected_message = translate("TEXT_INVITATION_ALREADY_USED")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._claim_restart, expected_message))

            return None

    setattr(
        CancelledTestBed, cancelled_step, getattr(CancelledTestBed, f"cancelled_{cancelled_step}")
    )

    await CancelledTestBed().run()


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_already_deleted(
    aqtbot, running_backend, backend, autoclose_dialog, alice, gui
):

    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id, greeter_user_id=alice.user_id
    )
    invitation_addr = BackendInvitationAddr.build(
        backend_addr=alice.organization_addr,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
    )
    await backend.invite.delete(
        organization_id=alice.organization_id,
        greeter=alice.user_id,
        token=invitation_addr.token,
        on=datetime_now(),
        reason=InvitationDeletedReason.CANCELLED,
    )

    await aqtbot.run(gui.add_instance, invitation_addr.to_url())

    def _assert_dialogs():
        assert len(autoclose_dialog.dialogs) == 1
        assert autoclose_dialog.dialogs == [("Error", translate("TEXT_INVITATION_ALREADY_USED"))]

    await aqtbot.wait_until(_assert_dialogs)


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_offline_backend(
    aqtbot, running_backend, backend, autoclose_dialog, alice, gui
):

    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id, greeter_user_id=alice.user_id
    )
    invitation_addr = BackendInvitationAddr.build(
        backend_addr=alice.organization_addr,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
    )
    with running_backend.offline():
        await aqtbot.run(gui.add_instance, invitation_addr.to_url())

    def _assert_dialogs():
        assert len(autoclose_dialog.dialogs) == 1
        assert autoclose_dialog.dialogs == [
            ("Error", translate("TEXT_INVITATION_BACKEND_NOT_AVAILABLE"))
        ]

    await aqtbot.wait_until(_assert_dialogs)


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_unknown_invitation(
    aqtbot, running_backend, backend, autoclose_dialog, alice, gui
):

    invitation_addr = BackendInvitationAddr.build(
        backend_addr=alice.organization_addr,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=uuid4(),
    )

    await aqtbot.run(gui.add_instance, invitation_addr.to_url())

    def _assert_dialogs():
        assert len(autoclose_dialog.dialogs) == 1
        assert autoclose_dialog.dialogs == [
            ("Error", translate("TEXT_CLAIM_DEVICE_INVITATION_NOT_FOUND"))
        ]

    await aqtbot.wait_until(_assert_dialogs)


@pytest.mark.gui
@pytest.mark.trio
async def test_claim_device_with_bad_start_arg(
    event_bus, core_config, gui_factory, autoclose_dialog
):
    bad_start_arg = "parsec://parsec.example.com/my_org?action=dummy&device_id=John%40pc&rvk=P25GRG3XPSZKBEKXYQFBOLERWQNEDY3AO43MVNZCLPXPKN63JRYQssss&token=1234ABCD"

    _ = await gui_factory(event_bus=event_bus, core_config=core_config, start_arg=bad_start_arg)

    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "The link is invalid."
