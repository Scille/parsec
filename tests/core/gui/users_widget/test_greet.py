# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from parsec.datetime import now as datetime_now
from PyQt5 import QtCore
from async_generator import asynccontextmanager
from functools import partial

from parsec.utils import start_task
from parsec.core.gui.lang import translate
from parsec.api.protocol import InvitationType, HumanHandle, InvitationDeletedReason
from parsec.core.types import BackendInvitationAddr
from parsec.core.backend_connection import backend_invited_cmds_factory
from parsec.core.invite import claimer_retrieve_info
from parsec.core.gui.users_widget import UserInvitationButton, UserButton
from parsec.core.gui.greet_user_widget import (
    GreetUserInstructionsWidget,
    GreetUserCheckInfoWidget,
    GreetUserCodeExchangeWidget,
    GreetUserWidget,
)

from tests.common import customize_fixtures


@pytest.fixture
def catch_greet_user_widget(widget_catcher_factory):
    return widget_catcher_factory(
        "parsec.core.gui.greet_user_widget.GreetUserInstructionsWidget",
        "parsec.core.gui.greet_user_widget.GreetUserCheckInfoWidget",
        "parsec.core.gui.greet_user_widget.GreetUserCodeExchangeWidget",
        "parsec.core.gui.greet_user_widget.GreetUserWidget",
    )


@pytest.fixture
def GreetUserTestBed(
    aqtbot, catch_greet_user_widget, autoclose_dialog, backend, running_backend, logged_gui
):
    class _GreetUserTestBed:
        def __init__(self):
            self.requested_human_handle = HumanHandle(email="pfry@pe.com", label="Philip J. Fry")
            self.requested_device_label = "PC1"
            self.steps_done = []

            # Set during bootstrap
            self.author = None
            self.users_widget = None
            self.invitation_widget = None
            self.invitation_addr = None
            self.greet_user_widget = None
            self.greet_user_information_widget = None
            self.cmds = None

            # Set by step 2
            self.greet_user_code_exchange_widget = None

            # Set by step 5
            self.claimer_claim_task = None
            self.greet_user_check_informations_widget = None

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
            claimer_email = self.requested_human_handle.email

            # Create new invitation

            invitation = await backend.invite.new_for_user(
                organization_id=author.organization_id,
                greeter_user_id=author.user_id,
                claimer_email=claimer_email,
            )
            invitation_addr = BackendInvitationAddr.build(
                backend_addr=author.organization_addr,
                organization_id=author.organization_id,
                invitation_type=InvitationType.USER,
                token=invitation.token,
            )

            # Switch to users page

            users_widget = await logged_gui.test_switch_to_users_widget()

            assert users_widget.layout_users.count() == 4

            invitation_widget = users_widget.layout_users.itemAt(0).widget()
            assert isinstance(invitation_widget, UserInvitationButton)
            assert invitation_widget.email == claimer_email

            # Click on the invitation button

            await aqtbot.mouse_click(invitation_widget.button_greet, QtCore.Qt.LeftButton)

            greet_user_widget = await catch_greet_user_widget()
            assert isinstance(greet_user_widget, GreetUserWidget)

            greet_user_information_widget = await catch_greet_user_widget()
            assert isinstance(greet_user_information_widget, GreetUserInstructionsWidget)

            def _greet_user_displayed():
                assert greet_user_widget.dialog.isVisible()
                assert greet_user_widget.isVisible()
                assert greet_user_widget.dialog.label_title.text() == "Greet a new user"
                assert greet_user_information_widget.isVisible()

            await aqtbot.wait_until(_greet_user_displayed)

            self.author = author
            self.users_widget = users_widget
            self.invitation_widget = invitation_widget
            self.invitation_addr = invitation_addr
            self.greet_user_widget = greet_user_widget
            self.greet_user_information_widget = greet_user_information_widget

            self.assert_initial_state()  # Sanity check

        async def bootstrap_after_restart(self):
            self.greet_user_information_widget = None
            self.greet_user_code_exchange_widget = None
            self.greet_user_check_informations_widget = None

            greet_user_widget = self.greet_user_widget
            greet_user_information_widget = await catch_greet_user_widget()
            assert isinstance(greet_user_information_widget, GreetUserInstructionsWidget)

            def _greet_user_displayed():
                assert greet_user_widget.dialog.isVisible()
                assert greet_user_widget.isVisible()
                assert greet_user_widget.dialog.label_title.text() == "Greet a new user"
                assert greet_user_information_widget.isVisible()

            await aqtbot.wait_until(_greet_user_displayed)

            self.greet_user_widget = greet_user_widget
            self.greet_user_information_widget = greet_user_information_widget

            self.assert_initial_state()  # Sanity check

        def assert_initial_state(self):
            assert self.greet_user_widget.isVisible()
            assert self.greet_user_information_widget.isVisible()
            assert self.greet_user_information_widget.button_start.isEnabled()
            if self.greet_user_code_exchange_widget:
                assert not self.greet_user_code_exchange_widget.isVisible()
            if self.greet_user_check_informations_widget:
                assert not self.greet_user_check_informations_widget.isVisible()

        async def step_1_start_greet(self):
            gui_w = self.greet_user_information_widget

            await aqtbot.mouse_click(gui_w.button_start, QtCore.Qt.LeftButton)

            def _greet_started():
                assert not gui_w.button_start.isEnabled()
                assert gui_w.button_start.text() == "Waiting for the other user..."

            await aqtbot.wait_until(_greet_started)

            return "step_2_start_claimer"

        async def step_2_start_claimer(self):
            gui_w = self.greet_user_information_widget

            self.claimer_initial_ctx = await claimer_retrieve_info(self.cmds)
            self.claimer_in_progress_ctx = await self.claimer_initial_ctx.do_wait_peer()
            greeter_sas = self.claimer_in_progress_ctx.greeter_sas

            guce_w = await catch_greet_user_widget()
            assert isinstance(guce_w, GreetUserCodeExchangeWidget)

            def _greeter_sas_displayed():
                assert not gui_w.isVisible()
                assert guce_w.isVisible()
                # We should be displaying the greeter SAS code
                assert guce_w.widget_greeter_code.isVisible()
                assert not guce_w.widget_claimer_code.isVisible()
                assert not guce_w.code_input_widget.isVisible()
                assert guce_w.line_edit_greeter_code.text() == greeter_sas

            await aqtbot.wait_until(_greeter_sas_displayed)

            self.greet_user_code_exchange_widget = guce_w

            return "step_3_exchange_greeter_sas"

        async def step_3_exchange_greeter_sas(self):
            guce_w = self.greet_user_code_exchange_widget

            self.claimer_in_progress_ctx = await self.claimer_in_progress_ctx.do_signify_trust()
            self.claimer_sas = self.claimer_in_progress_ctx.claimer_sas

            def _claimer_code_choices_displayed():
                assert not guce_w.widget_greeter_code.isVisible()
                assert guce_w.widget_claimer_code.isVisible()
                assert guce_w.code_input_widget.isVisible()
                assert guce_w.code_input_widget.code_layout.count() == 4
                # TODO: better check on codes

            await aqtbot.wait_until(_claimer_code_choices_displayed)

            return "step_4_exchange_claimer_sas"

        async def step_4_exchange_claimer_sas(self):
            guce_w = self.greet_user_code_exchange_widget

            # Pretent we have clicked on the right choice
            await aqtbot.run(guce_w.code_input_widget.good_code_clicked.emit)

            self.claimer_in_progress_ctx = await self.claimer_in_progress_ctx.do_wait_peer_trust()

            guci_w = await catch_greet_user_widget()
            assert isinstance(guci_w, GreetUserCheckInfoWidget)
            self.greet_user_check_informations_widget = guci_w

            def _wait_claimer_info():
                # TODO: unlike with greet_device_widget, there is no
                # `guce_w.label_wait_info` to check for waiting message
                assert not guce_w.widget_greeter_code.isVisible()
                assert not guce_w.widget_claimer_code.isVisible()
                assert not guce_w.isVisible()
                assert guci_w.isVisible()

            await aqtbot.wait_until(_wait_claimer_info)

            return "step_5_provide_claim_info"

        async def step_5_provide_claim_info(self):
            guci_w = self.greet_user_check_informations_widget

            # Must put this into a nursery given it will return during step 6

            async def _claimer_claim(in_progress_ctx, task_status=trio.TASK_STATUS_IGNORED):
                task_status.started()
                await in_progress_ctx.do_claim_user(
                    requested_device_label=self.requested_device_label,
                    requested_human_handle=self.requested_human_handle,
                )

            self.claimer_claim_task = await start_task(
                self.nursery, _claimer_claim, self.claimer_in_progress_ctx
            )

            def _check_info_displayed():
                assert guci_w.isVisible()
                assert guci_w.line_edit_user_full_name.text() == self.requested_human_handle.label
                assert guci_w.line_edit_user_email.text() == self.requested_human_handle.email
                assert guci_w.line_edit_device.text() == self.requested_device_label
                assert guci_w.label_warning.text() == translate(
                    "TEXT_LABEL_USER_ROLE_RECOMMANDATIONS"
                )

            await aqtbot.wait_until(_check_info_displayed)

            return "step_6_validate_claim_info"

        async def step_6_validate_claim_info(self):
            assert self.claimer_claim_task
            u_w = self.users_widget
            gu_w = self.greet_user_widget
            guci_w = self.greet_user_check_informations_widget

            # Finally confirm the claimer info and finish the greeting !
            await aqtbot.mouse_click(guci_w.button_create_user, QtCore.Qt.LeftButton)

            with trio.fail_after(1):
                await self.claimer_claim_task.join()

            def _greet_done():
                assert not gu_w.isVisible()
                assert autoclose_dialog.dialogs == [
                    ("", "The user was successfully greeter in your organization.")
                ]
                # User list should be updated
                assert u_w.layout_users.count() == 4
                user_widget = u_w.layout_users.itemAt(3).widget()
                assert isinstance(user_widget, UserButton)
                assert user_widget.user_info.human_handle == self.requested_human_handle

            await aqtbot.wait_until(_greet_done)

            return None  # Test is done \o/

    return _GreetUserTestBed


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_user(GreetUserTestBed):
    await GreetUserTestBed().run()


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
        "step_6_validate_claim_info",
    ],
)
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_user_offline(
    aqtbot, GreetUserTestBed, running_backend, autoclose_dialog, offline_step
):
    class OfflineTestBed(GreetUserTestBed):
        def _greet_aborted(self, expected_message):
            assert len(autoclose_dialog.dialogs) == 1
            assert autoclose_dialog.dialogs == [("Error", expected_message)]
            assert not self.greet_user_widget.isVisible()
            assert not self.greet_user_information_widget.isVisible()

        async def offline_step_1_start_greet(self):
            expected_message = translate("TEXT_GREET_USER_WAIT_PEER_ERROR")
            gui_w = self.greet_user_information_widget

            with running_backend.offline():
                await aqtbot.mouse_click(gui_w.button_start, QtCore.Qt.LeftButton)
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_2_start_claimer(self):
            expected_message = translate("TEXT_GREET_USER_WAIT_PEER_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_3_exchange_greeter_sas(self):
            expected_message = translate("TEXT_GREET_USER_WAIT_PEER_TRUST_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_GREET_USER_SIGNIFY_TRUST_ERROR")
            guce_w = self.greet_user_code_exchange_widget

            with running_backend.offline():
                await aqtbot.run(guce_w.code_input_widget.good_code_clicked.emit)
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_GREET_USER_GET_REQUESTS_ERROR")
            with running_backend.offline():
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

            return None

        async def offline_step_6_validate_claim_info(self):
            expected_message = translate("TEXT_GREET_USER_WAIT_PEER_TRUST_ERROR")
            guci_w = self.greet_user_check_informations_widget

            with running_backend.offline():
                self.nursery.cancel_scope.cancel()
                await aqtbot.mouse_click(guci_w.button_create_user, QtCore.Qt.LeftButton)
                await aqtbot.wait_until(partial(self._greet_aborted, expected_message))

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
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_user_reset_by_peer(aqtbot, GreetUserTestBed, autoclose_dialog, reset_step):
    class ResetTestBed(GreetUserTestBed):
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
            expected_message = translate("TEXT_GREET_USER_PEER_RESET")
            async with self._reset_claimer():
                await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_GREET_USER_PEER_RESET")
            guce_w = self.greet_user_code_exchange_widget

            # Pretent we have click on the right choice
            await aqtbot.run(guce_w.code_input_widget.good_code_clicked.emit)

            async with self._reset_claimer():
                await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_GREET_USER_PEER_RESET")
            async with self._reset_claimer():
                await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

        async def reset_step_6_validate_claim_info(self):
            expected_message = translate("TEXT_GREET_USER_PEER_RESET")
            guci_w = self.greet_user_check_informations_widget

            await self.claimer_claim_task.cancel_and_join()
            async with self._reset_claimer():

                await aqtbot.mouse_click(guci_w.button_create_user, QtCore.Qt.LeftButton)
                await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            await self.bootstrap_after_restart()
            return None

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
        "step_6_validate_claim_info",
    ],
)
@customize_fixtures(logged_gui_as_admin=True)
async def test_greet_user_invitation_cancelled(
    aqtbot, GreetUserTestBed, backend, autoclose_dialog, cancelled_step
):
    class CancelledTestBed(GreetUserTestBed):
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
            assert not self.greet_user_widget.isVisible()
            assert not self.greet_user_information_widget.isVisible()

        async def cancelled_step_1_start_greet(self):
            expected_message = translate("TEXT_GREET_USER_WAIT_PEER_ERROR")
            gui_w = self.greet_user_information_widget

            await self._cancel_invitation()

            await aqtbot.mouse_click(gui_w.button_start, QtCore.Qt.LeftButton)
            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_2_start_claimer(self):
            expected_message = translate("TEXT_GREET_USER_WAIT_PEER_ERROR")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_3_exchange_greeter_sas(self):
            expected_message = translate("TEXT_GREET_USER_WAIT_PEER_TRUST_ERROR")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_4_exchange_claimer_sas(self):
            expected_message = translate("TEXT_GREET_USER_SIGNIFY_TRUST_ERROR")
            guce_w = self.greet_user_code_exchange_widget

            await self._cancel_invitation()

            await aqtbot.run(guce_w.code_input_widget.good_code_clicked.emit)
            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_5_provide_claim_info(self):
            expected_message = translate("TEXT_GREET_USER_GET_REQUESTS_ERROR")
            await self._cancel_invitation()

            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

        async def cancelled_step_6_validate_claim_info(self):
            expected_message = translate("TEXT_GREET_USER_CREATE_USER_ERROR")
            guci_w = self.greet_user_check_informations_widget
            await self.claimer_claim_task.cancel_and_join()
            await self._cancel_invitation()

            await aqtbot.mouse_click(guci_w.button_create_user, QtCore.Qt.LeftButton)
            await aqtbot.wait_until(partial(self._greet_restart, expected_message))

            return None

    setattr(
        CancelledTestBed, cancelled_step, getattr(CancelledTestBed, f"cancelled_{cancelled_step}")
    )

    await CancelledTestBed().run()
