# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import QPoint, pyqtSignal
from PyQt5.QtWidgets import QApplication, QToolTip, QWidget
from structlog import get_logger

from parsec.api.protocol import DeviceID, DeviceName
from parsec.core.gui import desktop, validators
from parsec.core.gui.custom_dialogs import GreyedDialog, show_error, show_info
from parsec.core.gui.lang import translate as _
from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal
from parsec.core.gui.ui.invite_device_widget import Ui_InviteDeviceWidget
from parsec.core.invite_claim import (
    InviteClaimBackendOfflineError,
    InviteClaimError,
    InviteClaimTimeoutError,
)
from parsec.core.invite_claim import generate_invitation_token as core_generate_invitation_token
from parsec.core.invite_claim import invite_and_create_device as core_invite_and_create_device
from parsec.core.types import BackendOrganizationClaimDeviceAddr

logger = get_logger()


async def _do_registration(core, device, new_device_name, token):
    try:
        new_device_name = DeviceName(new_device_name)
    except ValueError as exc:
        raise JobResultError("registration-invite-bad-value") from exc

    try:
        await core_invite_and_create_device(
            device=device,
            new_device_name=new_device_name,
            token=token,
            keepalive=core.config.backend_connection_keepalive,
        )
    except InviteClaimTimeoutError:
        raise JobResultError("registration-invite-timeout")
    except InviteClaimBackendOfflineError as exc:
        raise JobResultError("registration-invite-offline") from exc
    except InviteClaimError as exc:
        if "already_exists" in str(exc):
            raise JobResultError("already_exists")
        raise JobResultError("registration-invite-error", info=str(exc))
    return new_device_name, token


class InviteDeviceWidget(QWidget, Ui_InviteDeviceWidget):
    registration_success = pyqtSignal()
    registration_error = pyqtSignal()

    def __init__(self, core, jobs_ctx, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.core = core
        self.dialog = None
        self.jobs_ctx = jobs_ctx
        self.registration_job = None
        self.widget_registration.hide()
        self.button_register.clicked.connect(self.register_device)
        self.button_copy_device.clicked.connect(
            self.copy_field(self.button_copy_device, self.line_edit_device)
        )
        self.button_copy_token.clicked.connect(
            self.copy_field(self.button_copy_token, self.line_edit_token)
        )
        self.button_copy_url.clicked.connect(
            self.copy_field(self.button_copy_url, self.line_edit_url)
        )
        self.button_copy_device.apply_style()
        self.button_copy_token.apply_style()
        self.button_copy_url.apply_style()
        self.registration_success.connect(self.on_registration_success)
        self.registration_error.connect(self.on_registration_error)
        self.line_edit_device_name.setValidator(validators.DeviceNameValidator())

    def on_close(self):
        self.cancel_registration()

    def copy_field(self, button, widget):
        def _inner_copy_field():
            desktop.copy_to_clipboard(widget.text())
            QToolTip.showText(
                button.mapToGlobal(QPoint(0, 0)), _("TEXT_INVITE_DEVICE_COPIED_TO_CLIPBOARD")
            )

        return _inner_copy_field

    def on_registration_error(self):
        self.line_edit_token.setText("")
        self.line_edit_url.setText("")
        self.line_edit_device.setText("")
        self.widget_registration.hide()
        self.button_register.show()
        self.line_edit_device_name.show()

        if not self.registration_job:
            return

        assert self.registration_job.is_finished()

        status = self.registration_job.status
        if status == "cancelled":
            return

        if status == "registration-invite-bad-value":
            errmsg = _("TEXT_INVITE_DEVICE_BAD_DEVICE_NAME")
        elif status == "already_exists":
            errmsg = _("TEXT_INVITE_DEVICE_ALREADY_EXISTS")
        elif status == "registration-invite-offline":
            errmsg = _("TEXT_INVITE_DEVICE_HOST_OFFLINE")
        elif status == "registration-invite-error":
            errmsg = _("TEXT_INVITE_DEVICE_WRONG_PARAMETERS")
        elif status == "registration-invite-timeout":
            errmsg = _("TEXT_INVITE_DEVICE_TIMEOUT")
        else:
            errmsg = _("TEXT_INVITE_DEVICE_UNKNOWN_FAILURE")
        show_error(self, errmsg, exception=self.registration_job.exc)

    def on_registration_success(self):
        assert self.registration_job.is_finished()
        assert self.registration_job.status == "ok"
        show_info(self, _("TEXT_INVITE_DEVICE_SUCCESS"))
        self.registration_job = None
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when inviting device")

    def cancel_registration(self):
        if self.registration_job:
            self.registration_job.cancel_and_join()

    def register_device(self):
        if not self.line_edit_device_name.text():
            show_error(self, _("TEXT_INVITE_DEVICE_EMPTY_DEVICE_NAME"))
            return

        try:
            new_device_id = DeviceID(
                f"{self.core.device.user_id}@{self.line_edit_device_name.text()}"
            )
        except ValueError as exc:
            show_error(self, _("TEXT_INVITE_DEVICE_BAD_DEVICE_NAME"), exception=exc)
            return
        token = core_generate_invitation_token()
        try:
            addr = BackendOrganizationClaimDeviceAddr.build(
                self.core.device.organization_addr, device_id=new_device_id
            )
        except ValueError as exc:
            show_error(self, _("TEXT_INVITE_DEVICE_WRONG_PARAMETERS"), exception=exc)
            return

        self.line_edit_device.setText(new_device_id.device_name)
        self.line_edit_device.setCursorPosition(0)
        self.line_edit_token.setText(token)
        self.line_edit_token.setCursorPosition(0)
        self.line_edit_url.setText(addr.to_url())
        self.line_edit_url.setCursorPosition(0)
        self.widget_registration.show()
        self.registration_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "registration_success"),
            ThreadSafeQtSignal(self, "registration_error"),
            _do_registration,
            core=self.core,
            device=self.core.device,
            new_device_name=new_device_id.device_name,
            token=token,
        )
        self.line_edit_device_name.hide()
        self.button_register.hide()

    @classmethod
    def exec_modal(cls, core, jobs_ctx, parent):
        w = cls(core=core, jobs_ctx=jobs_ctx)
        d = GreyedDialog(w, title=_("TEXT_INVITE_DEVICE_TITLE"), parent=parent)
        w.dialog = d
        w.line_edit_device_name.setFocus()
        return d.exec_()
