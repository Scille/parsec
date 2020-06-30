# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from structlog import get_logger

from parsec.api.protocol import DeviceID
from parsec.core.gui.custom_dialogs import GreyedDialog, show_error, show_info
from parsec.core.gui.lang import translate as _
from parsec.core.gui.password_validation import PasswordStrengthWidget, get_password_strength
from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal
from parsec.core.gui.ui.claim_device_widget import Ui_ClaimDeviceWidget
from parsec.core.invite_claim import InviteClaimBackendOfflineError, InviteClaimError
from parsec.core.invite_claim import claim_device as core_claim_device
from parsec.core.local_device import LocalDeviceAlreadyExistsError, save_device_with_password
from parsec.core.types import BackendOrganizationClaimDeviceAddr

logger = get_logger()


async def _do_claim_device(
    config,
    password: str,
    password_check: str,
    token: str,
    device_id: DeviceID,
    organization_addr: BackendOrganizationClaimDeviceAddr,
):
    if password != password_check:
        raise JobResultError("password-mismatch")
    if len(password) < 8:
        raise JobResultError("password-size")

    try:
        device = await core_claim_device(
            organization_addr=organization_addr.to_organization_addr(),
            new_device_id=device_id,
            token=token,
            keepalive=config.backend_connection_keepalive,
        )

    except InviteClaimBackendOfflineError as exc:
        raise JobResultError("backend-offline", info=str(exc)) from exc

    except InviteClaimError as exc:
        if "Cannot retrieve invitation creator" in str(exc):
            raise JobResultError("not_found", info=str(exc)) from exc
        else:
            raise JobResultError("refused-by-backend", info=str(exc)) from exc

    try:
        save_device_with_password(config.config_dir, device, password)
    except LocalDeviceAlreadyExistsError as exc:
        raise JobResultError("device-exists") from exc

    return device, password


class ClaimDeviceWidget(QWidget, Ui_ClaimDeviceWidget):
    claim_success = pyqtSignal()
    claim_error = pyqtSignal()

    def __init__(self, jobs_ctx, config, addr: BackendOrganizationClaimDeviceAddr):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.config = config
        self.addr = addr
        self.dialog = None
        self.label_instructions.setText(
            _("TEXT_CLAIM_DEVICE_INSTRUCTIONS_device-url-organization").format(
                device=self.addr.device_id,
                url=self.addr.to_url(),
                organization=self.addr.organization_id,
            )
        )
        pwd_str_widget = PasswordStrengthWidget()
        self.layout_password_strength.addWidget(pwd_str_widget)
        self.claim_device_job = None
        self.button_claim.clicked.connect(self.claim_clicked)
        self.line_edit_token.textChanged.connect(self.check_infos)
        self.line_edit_password.textChanged.connect(self.check_infos)
        self.line_edit_password_check.textChanged.connect(self.check_infos)
        self.line_edit_password.textChanged.connect(pwd_str_widget.on_password_change)
        self.claim_success.connect(self.on_claim_success)
        self.claim_error.connect(self.on_claim_error)

        if addr.token:
            self.line_edit_token.setText(addr.token)
        self.status = None
        self.check_infos()

    def on_claim_error(self):
        assert self.claim_device_job
        assert self.claim_device_job.is_finished()
        assert self.claim_device_job.status != "ok"

        status = self.claim_device_job.status
        if status != "cancelled":
            if status == "not_found":
                errmsg = _("TEXT_CLAIM_DEVICE_NOT_FOUND")
            elif status == "password-mismatch":
                errmsg = _("TEXT_CLAIM_DEVICE_PASSWORD_MISMATCH")
            elif status == "password-size":
                errmsg = _("TEXT_CLAIM_DEVICE_PASSWORD_COMPLEXITY_TOO_LOW")
            elif status == "bad-url":
                errmsg = _("TEXT_CLAIM_DEVICE_INVALID_URL")
            elif status == "bad-device_name":
                errmsg = _("TEXT_CLAIM_DEVICE_BAD_DEVICE_NAME")
            elif status == "bad-user_id":
                errmsg = _("TEXT_CLAIM_DEVICE_BAD_USER_NAME")
            elif status == "refused-by-backend":
                errmsg = _("TEXT_CLAIM_DEVICE_BACKEND_REFUSAL")
            elif status == "backend-offline":
                errmsg = _("TEXT_CLAIM_DEVICE_BACKEND_OFFLINE")
            else:
                errmsg = _("TEXT_CLAIM_DEVICE_UNKNOWN_FAILURE")
            show_error(self, errmsg, exception=self.claim_device_job.exc)
        self.claim_device_job = None
        self.check_infos()

    def on_claim_success(self):
        assert self.claim_device_job
        assert self.claim_device_job.is_finished()
        assert self.claim_device_job.status == "ok"

        self.status = self.claim_device_job.ret

        self.claim_device_job = None
        show_info(self, _("TEXT_CLAIM_DEVICE_SUCCESS"), button_text=_("ACTION_CONTINUE"))
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when claiming device")

    def cancel_claim(self):
        if self.claim_device_job:
            self.claim_device_job.cancel_and_join()

    def check_infos(self, _=""):
        if (
            len(self.line_edit_token.text())
            and not self.claim_device_job
            and len(self.line_edit_password.text())
            and get_password_strength(self.line_edit_password.text()) > 0
            and len(self.line_edit_password_check.text())
        ):
            self.button_claim.setDisabled(False)
        else:
            self.button_claim.setDisabled(True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return and not self.claim_device_job:
            self.claim_clicked()
        event.accept()

    def claim_clicked(self):
        assert not self.claim_device_job

        self.claim_device_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "claim_success"),
            ThreadSafeQtSignal(self, "claim_error"),
            _do_claim_device,
            config=self.config,
            password=self.line_edit_password.text(),
            password_check=self.line_edit_password_check.text(),
            token=self.line_edit_token.text(),
            device_id=self.addr.device_id,
            organization_addr=self.addr,
        )
        self.check_infos()

    def on_close(self):
        self.cancel_claim()

    @classmethod
    def exec_modal(cls, jobs_ctx, config, addr, parent):
        w = cls(jobs_ctx=jobs_ctx, config=config, addr=addr)
        d = GreyedDialog(w, _("TEXT_CLAIM_DEVICE_TITLE"), parent=parent)
        w.dialog = d
        w.line_edit_token.setFocus()
        if d.exec_() == QDialog.Accepted and w.status:
            return w.status
        return None
