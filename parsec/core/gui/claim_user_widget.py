# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from structlog import get_logger

from parsec.api.protocol import DeviceID
from parsec.core.gui import validators
from parsec.core.gui.custom_dialogs import GreyedDialog, show_error, show_info
from parsec.core.gui.desktop import get_default_device
from parsec.core.gui.lang import translate as _
from parsec.core.gui.password_validation import PasswordStrengthWidget, get_password_strength
from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal
from parsec.core.gui.ui.claim_user_widget import Ui_ClaimUserWidget
from parsec.core.invite_claim import InviteClaimBackendOfflineError, InviteClaimError
from parsec.core.invite_claim import claim_user as core_claim_user
from parsec.core.local_device import LocalDeviceAlreadyExistsError, save_device_with_password
from parsec.core.types import BackendOrganizationClaimUserAddr

logger = get_logger()


async def _do_claim_user(
    config,
    password: str,
    password_check: str,
    token: str,
    user_id: str,
    device_name: str,
    organization_addr: BackendOrganizationClaimUserAddr,
):
    if password != password_check:
        raise JobResultError("password-mismatch")
    if len(password) < 8:
        raise JobResultError("password-size")

    try:
        device_id = DeviceID(f"{user_id}@{device_name}")
    except ValueError as exc:
        raise JobResultError("bad-device_name") from exc

    try:
        device = await core_claim_user(
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
        raise JobResultError("user-exists") from exc

    return device, password


class ClaimUserWidget(QWidget, Ui_ClaimUserWidget):
    claim_success = pyqtSignal()
    claim_error = pyqtSignal()

    def __init__(self, jobs_ctx, config, addr: BackendOrganizationClaimUserAddr):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.config = config
        self.claim_user_job = None
        self.dialog = None
        self.addr = addr
        self.label_instructions.setText(
            _("TEXT_CLAIM_USER_INSTRUCTIONS_user-url-organization").format(
                user=self.addr.user_id,
                url=self.addr.to_url(),
                organization=self.addr.organization_id,
            )
        )
        pwd_str_widget = PasswordStrengthWidget()
        self.layout_password_strength.addWidget(pwd_str_widget)
        self.button_claim.clicked.connect(self.claim_clicked)
        self.line_edit_device.textChanged.connect(self.check_infos)
        self.line_edit_token.textChanged.connect(self.check_infos)
        self.line_edit_password.textChanged.connect(pwd_str_widget.on_password_change)
        self.line_edit_password.textChanged.connect(self.check_infos)
        self.line_edit_password_check.textChanged.connect(self.check_infos)
        self.claim_success.connect(self.on_claim_success)
        self.claim_error.connect(self.on_claim_error)
        self.line_edit_device.setValidator(validators.DeviceNameValidator())

        self.status = None

        if addr.token:
            self.line_edit_token.setText(addr.token)
        self.line_edit_device.setText(get_default_device())

        self.check_infos()

    def on_claim_error(self):
        assert self.claim_user_job
        assert self.claim_user_job.is_finished()
        assert self.claim_user_job.status != "ok"

        status = self.claim_user_job.status
        if status != "cancelled":
            if status == "not_found":
                errmsg = _("TEXT_CLAIM_USER_NOT_FOUND")
            elif status == "password-mismatch":
                errmsg = _("TEXT_CLAIM_USER_PASSWORD_MISMATCH")
            elif status == "password-size":
                errmsg = _("TEXT_CLAIM_USER_PASSWORD_COMPLEXITY")
            elif status == "bad-url":
                errmsg = _("TEXT_CLAIM_USER_INVALID_URL")
            elif status == "bad-device_name":
                errmsg = _("TEXT_CLAIM_USER_BAD_DEVICE_NAME")
            elif status == "bad-user_id":
                errmsg = _("TEXT_CLAIM_USER_BAD_USER_NAME")
            elif status == "refused-by-backend":
                errmsg = _("TEXT_CLAIM_USER_BACKEND_REFUSAL")
            elif status == "backend-offline":
                errmsg = _("TEXT_CLAIM_USER_BACKEND_OFFLINE")
            else:
                errmsg = _("TEXT_CLAIM_USER_UNKNOWN_FAILURE")
            show_error(self, errmsg, exception=self.claim_user_job.exc)
        self.claim_user_job = None
        self.check_infos()

    def on_claim_success(self):
        assert self.claim_user_job
        assert self.claim_user_job.is_finished()
        assert self.claim_user_job.status == "ok"

        self.status = self.claim_user_job.ret
        self.claim_user_job = None
        show_info(
            parent=self, message=_("TEXT_CLAIM_USER_SUCCESS"), button_text=_("ACTION_CONTINUE")
        )
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when claiming user")

    def cancel_claim(self):
        if self.claim_user_job:
            self.claim_user_job.cancel_and_join()

    def check_infos(self, _=""):
        if (
            len(self.line_edit_token.text())
            and len(self.line_edit_device.text())
            and not self.claim_user_job
            and len(self.line_edit_password.text())
            and get_password_strength(self.line_edit_password.text()) > 0
            and len(self.line_edit_password_check.text())
        ):
            self.button_claim.setDisabled(False)
        else:
            self.button_claim.setDisabled(True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return and not self.claim_user_job:
            self.claim_clicked()
        event.accept()

    def claim_clicked(self):
        assert not self.claim_user_job

        self.claim_user_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "claim_success"),
            ThreadSafeQtSignal(self, "claim_error"),
            _do_claim_user,
            config=self.config,
            password=self.line_edit_password.text(),
            password_check=self.line_edit_password_check.text(),
            token=self.line_edit_token.text(),
            user_id=str(self.addr.user_id),
            device_name=self.line_edit_device.text(),
            organization_addr=self.addr,
        )
        self.check_infos()

    def on_close(self):
        self.cancel_claim()

    @classmethod
    def exec_modal(cls, jobs_ctx, config, addr, parent):
        w = cls(jobs_ctx=jobs_ctx, config=config, addr=addr)
        d = GreyedDialog(w, _("TEXT_CLAIM_USER_TITLE"), parent=parent)
        w.dialog = d
        w.line_edit_token.setFocus()
        if d.exec_() == QDialog.Accepted and w.status:
            return w.status
        return None
