# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QWidget, QDialog

from enum import IntEnum

import trio

from parsec.api.data import UserProfile
from parsec.api.protocol import HumanHandle
from parsec.core.backend_connection import backend_authenticated_cmds_factory, BackendNotAvailable
from parsec.core.invite import UserGreetInitialCtx, InviteError

from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal
from parsec.core.gui.custom_dialogs import show_error, GreyedDialog, show_info
from parsec.core.gui.lang import translate as _

from parsec.core.gui.ui.greet_user_widget import Ui_GreetUserWidget
from parsec.core.gui.ui.greet_user_code_exchange_widget import Ui_GreetUserCodeExchangeWidget
from parsec.core.gui.ui.greet_user_check_info_widget import Ui_GreetUserCheckInfoWidget
from parsec.core.gui.ui.greet_user_instructions_widget import Ui_GreetUserInstructionsWidget


class Greeter:
    class Step(IntEnum):
        WaitPeer = 1
        GetGreeterSas = 2
        WaitPeerTrust = 3
        GetClaimerSas = 4
        SignifyTrust = 5
        GetClaimRequests = 6
        CreateNewUser = 7

    def __init__(self):
        self.main_mc_send, self.main_mc_recv = trio.open_memory_channel(0)
        self.job_mc_send, self.job_mc_recv = trio.open_memory_channel(0)

    async def run(self, device, config, token):
        try:
            async with backend_authenticated_cmds_factory(
                addr=device.organization_addr,
                device_id=device.device_id,
                signing_key=device.signing_key,
                keepalive=config.backend_connection_keepalive,
            ) as cmds:
                initial_ctx = UserGreetInitialCtx(cmds=cmds, token=token)
                r = await self.main_mc_recv.receive()

                assert r == self.Step.WaitPeer
                try:
                    in_progress_ctx = await initial_ctx.do_wait_peer()
                    await self.job_mc_send.send((True, None))
                except Exception as exc:
                    await self.job_mc_send.send((False, exc))

                r = await self.main_mc_recv.receive()

                assert r == self.Step.GetGreeterSas
                await self.job_mc_send.send(in_progress_ctx.greeter_sas)

                r = await self.main_mc_recv.receive()

                assert r == self.Step.WaitPeerTrust
                try:
                    in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()
                    await self.job_mc_send.send((True, None))
                except Exception as exc:
                    await self.job_mc_send.send((False, exc))

                r = await self.main_mc_recv.receive()

                assert r == self.Step.GetClaimerSas
                try:
                    choices = in_progress_ctx.generate_claimer_sas_choices(size=4)
                    await self.job_mc_send.send((True, None, in_progress_ctx.claimer_sas, choices))
                except Exception as exc:
                    await self.job_mc_send.send((False, exc, None, None))

                r = await self.main_mc_recv.receive()

                assert r == self.Step.SignifyTrust
                try:
                    in_progress_ctx = await in_progress_ctx.do_signify_trust()
                    await self.job_mc_send.send((True, None))
                except Exception as exc:
                    await self.job_mc_send.send(False, exc)

                r = await self.main_mc_recv.receive()

                assert r == self.Step.GetClaimRequests
                try:
                    in_progress_ctx = await in_progress_ctx.do_get_claim_requests()
                    await self.job_mc_send.send(
                        (
                            True,
                            None,
                            in_progress_ctx.requested_human_handle,
                            in_progress_ctx.requested_device_label,
                        )
                    )
                except Exception as exc:
                    await self.job_mc_send.send((False, exc, None, None))

                r = await self.main_mc_recv.receive()

                assert r == self.Step.CreateNewUser
                try:
                    human_handle, device_label, profile = await self.main_mc_recv.receive()
                    await in_progress_ctx.do_create_new_user(
                        author=device,
                        device_label=device_label,
                        human_handle=human_handle,
                        profile=profile,
                    )
                    await self.job_mc_send.send((True, None))
                except InviteError as exc:
                    await self.job_mc_send.send((False, exc))
                except Exception as exc:
                    await self.job_mc_send.send((False, exc))
        except BackendNotAvailable as exc:
            raise JobResultError(status="backend-not-available", origin=exc)
        except Exception as exc:
            raise JobResultError(status="unknown", origin=exc)

    async def wait_peer(self):
        await self.main_mc_send.send(self.Step.WaitPeer)
        r, exc = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="wait-peer-failed", origin=exc)

    async def get_greeter_sas(self):
        await self.main_mc_send.send(self.Step.GetGreeterSas)
        greeter_sas = await self.job_mc_recv.receive()
        return greeter_sas

    async def wait_peer_trust(self):
        await self.main_mc_send.send(self.Step.WaitPeerTrust)
        r, exc = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="wait-peer-trust-failed", origin=exc)

    async def get_claimer_sas(self):
        await self.main_mc_send.send(self.Step.GetClaimerSas)
        r, exc, claimer_sas, choices = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="get-claimer-sas-failed", origin=exc)
        return claimer_sas, choices

    async def signify_trust(self):
        await self.main_mc_send.send(self.Step.SignifyTrust)
        r, exc = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="signify-trust-failed", origin=exc)

    async def get_claim_requests(self):
        await self.main_mc_send.send(self.Step.GetClaimRequests)
        r, exc, human_handle, device_label = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="get-claim-request-failed", origin=exc)
        return human_handle, device_label

    async def create_new_user(self, human_handle, device_label, profile):
        await self.main_mc_send.send(self.Step.CreateNewUser)
        await self.main_mc_send.send((human_handle, device_label, profile))
        r, exc = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="create-new-user-failed", origin=exc)


class GreetUserInstructionsWidget(QWidget, Ui_GreetUserInstructionsWidget):
    succeeded = pyqtSignal()
    failed = pyqtSignal()

    wait_peer_success = pyqtSignal()
    wait_peer_error = pyqtSignal()

    def __init__(self, jobs_ctx, greeter):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.greeter = greeter
        self.wait_peer_job = None
        self.wait_peer_success.connect(self._on_wait_peer_success)
        self.wait_peer_error.connect(self._on_wait_peer_error)
        self.button_start.clicked.connect(self._on_button_start_clicked)

    def _on_button_start_clicked(self):
        self.button_start.setDisabled(True)
        self.button_start.setText(_("TEXT_GREET_USER_WAITING"))
        self.wait_peer_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "wait_peer_success"),
            ThreadSafeQtSignal(self, "wait_peer_error"),
            self.greeter.wait_peer,
        )

    def _on_wait_peer_success(self):
        assert self.wait_peer_job
        assert self.wait_peer_job.is_finished()
        assert self.wait_peer_job.status == "ok"
        self.greeter_sas = self.wait_peer_job.ret
        self.wait_peer_job = None
        self.succeeded.emit()

    def _on_wait_peer_error(self):
        assert self.wait_peer_job
        assert self.wait_peer_job.is_finished()
        assert self.wait_peer_job.status != "ok"
        if self.wait_peer_job.status != "cancelled":
            msg = _("TEXT_GREET_USER_WAIT_PEER_ERROR")
            exc = None
            if self.wait_peer_job.exc:
                exc = self.wait_peer_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.wait_peer_job = None
            self.failed.emit()
        else:
            self.wait_peer_job = None


class GreetUserCheckInfoWidget(QWidget, Ui_GreetUserCheckInfoWidget):
    succeeded = pyqtSignal()
    failed = pyqtSignal()

    get_requests_success = pyqtSignal()
    get_requests_error = pyqtSignal()

    create_user_success = pyqtSignal()
    create_user_error = pyqtSignal()

    def __init__(self, jobs_ctx, greeter):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.greeter = greeter
        self.get_requests_job = None
        self.create_user_job = None

        self.widget_info.hide()
        self.label_waiting.show()

        self.line_edit_user_full_name.textChanged.connect(self.check_infos)
        self.line_edit_user_email.textChanged.connect(self.check_infos)
        self.line_edit_device.textChanged.connect(self.check_infos)

        # self.combo_profile.addItem(_("TEXT_USER_PROFILE_OUTSIDER"), UserProfile.OUTSIDER)
        self.combo_profile.addItem(_("TEXT_USER_PROFILE_STANDARD"), UserProfile.STANDARD)
        self.combo_profile.addItem(_("TEXT_USER_PROFILE_ADMIN"), UserProfile.ADMIN)
        self.combo_profile.setCurrentIndex(0)

        self.get_requests_success.connect(self._on_get_requests_success)
        self.get_requests_error.connect(self._on_get_requests_error)
        self.create_user_success.connect(self._on_create_user_success)
        self.create_user_error.connect(self._on_create_user_error)
        self._get_claim_requests()
        self.button_create_user.clicked.connect(self._on_create_user_clicked)

    def check_infos(self, _=""):
        if self.line_edit_user_full_name.text() and self.line_edit_device.text():
            self.button_create_user.setDisabled(False)
        else:
            self.button_create_user.setDisabled(True)

    def _on_create_user_clicked(self):
        assert not self.create_user_job
        handle = None
        device_label = self.line_edit_device.text()
        try:
            handle = HumanHandle(
                label=self.line_edit_user_full_name.text(), email=self.line_edit_user_email.text()
            )
        except ValueError as exc:
            show_error(self, _("TEXT_GREET_USER_INVALID_HUMAN_HANDLE"), exception=exc)
            return
        self.button_create_user.setDisabled(True)
        self.button_create_user.setText(_("TEXT_GREET_USER_WAITING"))
        self.create_user_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "create_user_success"),
            ThreadSafeQtSignal(self, "create_user_error"),
            self.greeter.create_new_user,
            human_handle=handle,
            device_label=device_label,
            profile=self.combo_profile.currentData(),
        )

    def _get_claim_requests(self):
        self.get_requests_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "get_requests_success"),
            ThreadSafeQtSignal(self, "get_requests_error"),
            self.greeter.get_claim_requests,
        )

    def _on_create_user_success(self):
        assert self.create_user_job
        assert self.create_user_job.is_finished()
        assert self.create_user_job.status == "ok"
        self.create_user_job = None
        self.succeeded.emit()

    def _on_create_user_error(self):
        assert self.create_user_job
        assert self.create_user_job.is_finished()
        assert self.create_user_job.status != "ok"
        if self.create_user_job.status != "cancelled":
            msg = _("TEXT_GREET_USER_CREATE_USER_ERROR")
            exc = None
            if self.create_user_job.exc:
                exc = self.create_user_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.create_user_job = None
            self.failed.emit()
        else:
            self.create_user_job = None

    def _on_get_requests_success(self):
        assert self.get_requests_job
        assert self.get_requests_job.is_finished()
        assert self.get_requests_job.status == "ok"

        human_handle, device_label = self.get_requests_job.ret
        self.get_requests_job = None
        self.label_waiting.hide()
        self.widget_info.show()
        self.line_edit_user_full_name.setText(human_handle.label)
        self.line_edit_user_email.setText(human_handle.email)
        self.line_edit_device.setText(device_label)
        self.check_infos()

    def _on_get_requests_error(self):
        assert self.get_requests_job
        assert self.get_requests_job.is_finished()
        assert self.get_requests_job.status != "ok"
        if self.get_requests_job.status != "cancelled":
            msg = _("TEXT_GREET_USER_GET_REQUESTS_ERROR")
            exc = None
            if self.get_requests_job.exc:
                exc = self.get_requests_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.get_requests_job = None
        else:
            self.get_requests_job = None


class GreetUserCodeExchangeWidget(QWidget, Ui_GreetUserCodeExchangeWidget):
    succeeded = pyqtSignal()
    failed = pyqtSignal()

    signify_trust_success = pyqtSignal()
    signify_trust_error = pyqtSignal()

    wait_peer_trust_success = pyqtSignal()
    wait_peer_trust_error = pyqtSignal()

    get_claimer_sas_success = pyqtSignal()
    get_claimer_sas_error = pyqtSignal()

    get_greeter_sas_success = pyqtSignal()
    get_greeter_sas_error = pyqtSignal()

    def __init__(self, jobs_ctx, greeter):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.greeter = greeter

        self.wait_peer_trust_job = None
        self.signify_trust_job = None
        self.get_claimer_sas_job = None
        self.get_greeter_sas_job = None

        self.widget_claimer_code.hide()

        font = self.line_edit_greeter_code.font()
        font.setBold(True)
        font.setLetterSpacing(QFont.PercentageSpacing, 180)
        self.line_edit_greeter_code.setFont(font)

        self.code_input_widget.good_code_clicked.connect(self._on_good_claimer_code_clicked)
        self.code_input_widget.wrong_code_clicked.connect(self._on_wrong_claimer_code_clicked)
        self.code_input_widget.none_clicked.connect(self._on_none_clicked)

        self.signify_trust_success.connect(self._on_signify_trust_success)
        self.signify_trust_error.connect(self._on_signify_trust_error)
        self.wait_peer_trust_success.connect(self._on_wait_peer_trust_success)
        self.wait_peer_trust_error.connect(self._on_wait_peer_trust_error)
        self.get_greeter_sas_success.connect(self._on_get_greeter_sas_success)
        self.get_greeter_sas_error.connect(self._on_get_greeter_sas_error)
        self.get_claimer_sas_success.connect(self._on_get_claimer_sas_success)
        self.get_claimer_sas_error.connect(self._on_get_claimer_sas_error)

        self.get_greeter_sas_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "get_greeter_sas_success"),
            ThreadSafeQtSignal(self, "get_greeter_sas_error"),
            self.greeter.get_greeter_sas,
        )

    def _on_good_claimer_code_clicked(self):
        self.widget_claimer_code.setDisabled(True)
        self.signify_trust_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "signify_trust_success"),
            ThreadSafeQtSignal(self, "signify_trust_error"),
            self.greeter.signify_trust,
        )

    def _on_wrong_claimer_code_clicked(self):
        show_error(self, _("TEXT_GREET_USER_INVALID_CODE_CLICKED"))
        self.failed.emit()

    def _on_none_clicked(self):
        show_info(self, _("TEXT_GREET_USER_NONE_CODE_CLICKED"))
        self.failed.emit()

    def _on_get_greeter_sas_success(self):
        assert self.get_greeter_sas_job
        assert self.get_greeter_sas_job.is_finished()
        assert self.get_greeter_sas_job.status == "ok"
        greeter_sas = self.get_greeter_sas_job.ret
        self.line_edit_greeter_code.setText(str(greeter_sas))
        self.get_greeter_sas_job = None
        self.wait_peer_trust_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "wait_peer_trust_success"),
            ThreadSafeQtSignal(self, "wait_peer_trust_error"),
            self.greeter.wait_peer_trust,
        )

    def _on_get_greeter_sas_error(self):
        assert self.get_greeter_sas_job
        assert self.get_greeter_sas_job.is_finished()
        assert self.get_greeter_sas_job.status == "ok"
        if self.get_greeter_sas_job.status != "cancelled":
            msg = _("TEXT_GREET_USER_GET_GREETER_SAS_ERROR")
            exc = None
            if self.get_greeter_sas_job.exc:
                exc = self.get_greeter_sas_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.get_greeter_sas_job = None
        else:
            self.get_greeter_sas_job = None

    def _on_get_claimer_sas_success(self):
        assert self.get_claimer_sas_job
        assert self.get_claimer_sas_job.is_finished()
        assert self.get_claimer_sas_job.status == "ok"
        claimer_sas, choices = self.get_claimer_sas_job.ret
        self.get_claimer_sas_job = None
        self.widget_greeter_code.hide()
        self.widget_claimer_code.show()
        self.code_input_widget.set_choices(choices, claimer_sas)

    def _on_get_claimer_sas_error(self):
        assert self.get_claimer_sas_job
        assert self.get_claimer_sas_job.is_finished()
        assert self.get_claimer_sas_job.status == "ok"
        if self.get_claimer_sas_job.status != "cancelled":
            msg = _("TEXT_GREET_USER_GET_CLAIMER_SAS_ERROR")
            exc = None
            if self.get_claimer_sas_job.exc:
                exc = self.get_claimer_sas_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.get_claimer_sas_job = None
        else:
            self.get_claimer_sas_job = None

    def _on_signify_trust_success(self):
        assert self.signify_trust_job
        assert self.signify_trust_job.is_finished()
        assert self.signify_trust_job.status == "ok"
        self.signify_trust_job = None
        self.succeeded.emit()

    def _on_signify_trust_error(self):
        assert self.signify_trust_job
        assert self.signify_trust_job.is_finished()
        assert self.signify_trust_job.status != "ok"
        if self.signify_trust_job.status != "cancelled":
            msg = _("TEXT_GREET_USER_SIGNIFY_TRUST_ERROR")
            exc = None
            if self.signify_trust_job.exc:
                exc = self.signify_trust_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.signify_trust_job = None
        else:
            self.signify_trust_job = None

    def _on_wait_peer_trust_success(self):
        assert self.wait_peer_trust_job
        assert self.wait_peer_trust_job.is_finished()
        assert self.wait_peer_trust_job.status == "ok"
        self.wait_peer_trust_job = None
        self.get_claimer_sas_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "get_claimer_sas_success"),
            ThreadSafeQtSignal(self, "get_claimer_sas_error"),
            self.greeter.get_claimer_sas,
        )

    def _on_wait_peer_trust_error(self):
        assert self.wait_peer_trust_job
        assert self.wait_peer_trust_job.is_finished()
        assert self.wait_peer_trust_job.status != "ok"
        if self.wait_peer_trust_job.status != "cancelled":
            msg = _("TEXT_GREET_USER_WAIT_PEER_TRUST_ERROR")
            exc = None
            if self.wait_peer_trust_job.exc:
                exc = self.wait_peer_trust_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.wait_peer_trust_job = None
        else:
            self.wait_peer_trust_job = None


class GreetUserWidget(QWidget, Ui_GreetUserWidget):
    greeter_success = pyqtSignal()
    greeter_error = pyqtSignal()

    def __init__(self, core, jobs_ctx, token):
        super().__init__()
        self.setupUi(self)
        self.core = core
        self.jobs_ctx = jobs_ctx
        self.token = token
        self.status = None
        self.dialog = None
        self.greeter = Greeter()
        self.greeter_job = None
        self.greeter_success.connect(self._on_greeter_success)
        self.greeter_error.connect(self._on_greeter_error)
        self._run_greeter()

    def _run_greeter(self):
        self.greeter_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "greeter_success"),
            ThreadSafeQtSignal(self, "greeter_error"),
            self.greeter.run,
            device=self.core.device,
            config=self.core.config,
            token=self.token,
        )
        self._goto_page1()

    def restart(self):
        self.cancel()
        self.status = None
        self.greeter = Greeter()
        self._run_greeter()

    def _on_page_failure_reboot(self):
        self.restart()

    def _on_page_failure_stop(self):
        self.dialog.accept()

    def _goto_page1(self):
        item = self.main_layout.takeAt(0)
        if item:
            current_page = item.widget()
            if current_page:
                current_page.hide()
                current_page.setParent(None)
        page = GreetUserInstructionsWidget(self.jobs_ctx, self.greeter)
        page.succeeded.connect(self._goto_page2)
        page.failed.connect(self._on_page_failure_reboot)
        self.main_layout.addWidget(page)

    def _goto_page2(self):
        current_page = self.main_layout.takeAt(0).widget()
        current_page.hide()
        current_page.setParent(None)
        page = GreetUserCodeExchangeWidget(self.jobs_ctx, self.greeter)
        page.succeeded.connect(self._goto_page3)
        page.failed.connect(self._on_page_failure_reboot)
        self.main_layout.addWidget(page)

    def _goto_page3(self):
        current_page = self.main_layout.takeAt(0).widget()
        current_page.hide()
        current_page.setParent(None)
        page = GreetUserCheckInfoWidget(self.jobs_ctx, self.greeter)
        page.succeeded.connect(self._on_finished)
        page.failed.connect(self._on_page_failure_reboot)
        self.main_layout.addWidget(page)

    def _on_finished(self):
        show_info(self, _("TEXT_USER_GREET_SUCCESSFUL"))
        self.dialog.accept()

    def _on_greeter_success(self):
        assert self.greeter_job
        assert self.greeter_job.is_finished()
        assert self.greeter_job.status == "ok"
        self.greeter_job = None

    def _on_greeter_error(self):
        assert self.greeter_job
        assert self.greeter_job.is_finished()
        assert self.greeter_job.status != "ok"
        if self.greeter_job.status != "cancelled":
            msg = ""
            if self.greeter_job.status == "backend-not-available":
                msg = _("TEXT_INVITATION_BACKEND_NOT_AVAILABLE")
            else:
                msg = _("TEXT_GREET_USER_UNKNOWN_ERROR")
            exc = None
            if self.greeter_job.exc:
                exc = self.greeter_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
        self.greeter_job = None
        self._on_page_failure_stop()

    def cancel(self):
        item = self.main_layout.itemAt(0)
        if item:
            current_page = item.widget()
            if current_page and getattr(current_page, "cancel", None):
                current_page.cancel()
        if self.greeter_job:
            self.greeter_job.cancel_and_join()

    def on_close(self):
        self.cancel()

    @classmethod
    def exec_modal(cls, core, jobs_ctx, token, parent):
        w = cls(core=core, jobs_ctx=jobs_ctx, token=token)
        d = GreyedDialog(w, _("TEXT_GREET_USER_TITLE"), parent=parent, width=1000)
        w.dialog = d
        if d.exec_() == QDialog.Accepted and w.status:
            return w.status
        return None
