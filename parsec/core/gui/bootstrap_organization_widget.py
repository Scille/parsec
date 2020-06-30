# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from structlog import get_logger

from parsec.api.data import DeviceCertificateContent, UserCertificateContent, UserProfile
from parsec.api.protocol import DeviceID
from parsec.core.backend_connection import (
    BackendConnectionError,
    BackendConnectionRefused,
    BackendNotAvailable,
    apiv1_backend_anonymous_cmds_factory,
)
from parsec.core.gui import validators
from parsec.core.gui.custom_dialogs import GreyedDialog, show_error, show_info
from parsec.core.gui.desktop import get_default_device
from parsec.core.gui.lang import translate as _
from parsec.core.gui.password_validation import PasswordStrengthWidget, get_password_strength
from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal
from parsec.core.gui.ui.bootstrap_organization_widget import Ui_BootstrapOrganizationWidget
from parsec.core.local_device import (
    LocalDeviceAlreadyExistsError,
    generate_new_device,
    save_device_with_password,
)
from parsec.core.types import BackendOrganizationBootstrapAddr
from parsec.crypto import SigningKey

logger = get_logger()


async def _do_bootstrap_organization(
    config_dir,
    password: str,
    password_check: str,
    user_id: str,
    device_name: str,
    bootstrap_addr: BackendOrganizationBootstrapAddr,
):
    if password != password_check:
        raise JobResultError("password-mismatch")
    if len(password) < 8:
        raise JobResultError("password-size")

    try:
        device_id = DeviceID(f"{user_id}@{device_name}")
    except ValueError as exc:
        raise JobResultError("bad-device_name") from exc

    root_signing_key = SigningKey.generate()
    root_verify_key = root_signing_key.verify_key
    organization_addr = bootstrap_addr.generate_organization_addr(root_verify_key)

    try:
        device = generate_new_device(device_id, organization_addr, profile=UserProfile.ADMIN)
        save_device_with_password(config_dir, device, password)

    except LocalDeviceAlreadyExistsError as exc:
        raise JobResultError("user-exists") from exc

    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=None,
        timestamp=now,
        user_id=device.user_id,
        human_handle=None,
        public_key=device.public_key,
        profile=device.profile,
    ).dump_and_sign(root_signing_key)
    device_certificate = DeviceCertificateContent(
        author=None,
        timestamp=now,
        device_id=device_id,
        device_label=None,
        verify_key=device.verify_key,
    ).dump_and_sign(root_signing_key)

    try:
        async with apiv1_backend_anonymous_cmds_factory(bootstrap_addr) as cmds:
            rep = await cmds.organization_bootstrap(
                organization_id=bootstrap_addr.organization_id,
                bootstrap_token=bootstrap_addr.token,
                root_verify_key=root_verify_key,
                # Regular certificates compatible with redacted here
                redacted_user_certificate=user_certificate,
                redacted_device_certificate=device_certificate,
            )

            if rep["status"] == "already_bootstrapped":
                raise JobResultError("already-bootstrapped", info=str(rep))
            elif rep["status"] == "not_found":
                raise JobResultError("invalid-url", info=str(rep))
            elif rep["status"] != "ok":
                raise JobResultError("refused-by-backend", info=str(rep))
        return device, password
    except BackendConnectionRefused as exc:
        raise JobResultError("invalid-url", info=str(exc)) from exc

    except BackendNotAvailable as exc:
        raise JobResultError("backend-offline", info=str(exc)) from exc

    except BackendConnectionError as exc:
        raise JobResultError("refused-by-backend", info=str(exc)) from exc


class BootstrapOrganizationWidget(QWidget, Ui_BootstrapOrganizationWidget):
    bootstrap_success = pyqtSignal()
    bootstrap_error = pyqtSignal()

    def __init__(self, jobs_ctx, config, addr: BackendOrganizationBootstrapAddr):
        super().__init__()
        self.setupUi(self)
        self.dialog = None
        self.jobs_ctx = jobs_ctx
        self.config = config
        self.addr = addr
        self.label_instructions.setText(
            _("TEXT_BOOTSTRAP_ORG_INSTRUCTIONS_url-organization").format(
                url=self.addr.to_url(), organization=self.addr.organization_id
            )
        )
        self.bootstrap_job = None
        self.button_bootstrap.clicked.connect(self.bootstrap_clicked)
        pwd_str_widget = PasswordStrengthWidget()
        self.layout_password_strength.addWidget(pwd_str_widget)
        self.line_edit_password.textChanged.connect(pwd_str_widget.on_password_change)
        self.line_edit_login.textChanged.connect(self.check_infos)
        self.line_edit_device.textChanged.connect(self.check_infos)
        self.line_edit_password.textChanged.connect(self.check_infos)
        self.line_edit_password_check.textChanged.connect(self.check_infos)
        self.line_edit_login.setValidator(validators.UserIDValidator())
        self.line_edit_device.setValidator(validators.DeviceNameValidator())
        self.bootstrap_success.connect(self.on_bootstrap_success)
        self.bootstrap_error.connect(self.on_bootstrap_error)

        self.line_edit_device.setText(get_default_device())

        self.status = None

        self.check_infos()

    def on_bootstrap_error(self):
        assert self.bootstrap_job
        assert self.bootstrap_job.is_finished()
        assert self.bootstrap_job.status != "ok"

        status = self.bootstrap_job.status

        if status == "cancelled":
            self.bootstrap_job = None
            return

        if status == "invalid-url" or status == "bad-url":
            errmsg = _("TEXT_BOOTSTRAP_ORG_INVALID_URL")
        elif status == "already-bootstrapped":
            errmsg = _("TEXT_BOOTSTRAP_ORG_ALREADY_BOOTSTRAPPED")
        elif status == "user-exists":
            errmsg = _("TEXT_BOOTSTRAP_ORG_USER_EXISTS")
        elif status == "password-mismatch":
            errmsg = _("TEXT_BOOTSTRAP_ORG_PASSWORD_MISMATCH")
        elif status == "password-size":
            errmsg = _("TEXT_BOOTSTRAP_ORG_PASSWORD_COMPLEXITY_TOO_LOW")
        elif status == "bad-device_name":
            errmsg = _("TEXT_BOOTSTRAP_ORG_BAD_DEVICE_NAME")
        elif status == "bad-user_id":
            errmsg = _("TEXT_BOOTSTRAP_ORG_BAD_USER_NAME")
        elif status == "bad-api-version":
            errmsg = _("TEXT_BOOTSTRAP_ORG_BAD_API_VERSION")
        elif status == "refused-by-backend":
            errmsg = _("TEXT_BOOTSTRAP_ORG_BACKEND_REFUSAL")
        elif status == "backend-offline":
            errmsg = _("TEXT_BOOTSTRAP_ORG_BACKEND_OFFLINE")
        else:
            errmsg = _("TEXT_BOOTSTRAP_ORG_UNKNOWN_FAILURE")
        show_error(self, errmsg, exception=self.bootstrap_job.exc)
        self.bootstrap_job = None
        self.check_infos()

    def on_bootstrap_success(self):
        assert self.bootstrap_job
        assert self.bootstrap_job.is_finished()
        assert self.bootstrap_job.status == "ok"

        self.button_bootstrap.setDisabled(False)
        self.status = self.bootstrap_job.ret
        self.bootstrap_job = None
        self.check_infos()
        show_info(
            parent=self,
            message=_("TEXT_BOOTSTRAP_ORG_SUCCESS_organization").format(
                organization=self.addr.organization_id
            ),
            button_text=_("ACTION_CONTINUE"),
        )
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when bootstraping")

    def bootstrap_clicked(self):
        assert not self.bootstrap_job

        self.bootstrap_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "bootstrap_success"),
            ThreadSafeQtSignal(self, "bootstrap_error"),
            _do_bootstrap_organization,
            config_dir=self.config.config_dir,
            password=self.line_edit_password.text(),
            password_check=self.line_edit_password_check.text(),
            user_id=self.line_edit_login.text(),
            device_name=self.line_edit_device.text(),
            bootstrap_addr=self.addr,
        )
        self.check_infos()

    def on_close(self):
        self.cancel_bootstrap()

    def cancel_bootstrap(self):
        if self.bootstrap_job:
            self.bootstrap_job.cancel_and_join()
        self.check_infos()

    def check_infos(self, _=""):
        if (
            len(self.line_edit_login.text())
            and len(self.line_edit_device.text())
            and not self.bootstrap_job
            and len(self.line_edit_password.text())
            and get_password_strength(self.line_edit_password.text()) > 0
            and len(self.line_edit_password_check.text())
        ):
            self.button_bootstrap.setDisabled(False)
        else:
            self.button_bootstrap.setDisabled(True)

    @classmethod
    def exec_modal(cls, jobs_ctx, config, addr, parent):
        w = cls(jobs_ctx=jobs_ctx, config=config, addr=addr)
        d = GreyedDialog(w, _("TEXT_BOOTSTRAP_ORG_TITLE"), parent=parent)
        w.dialog = d
        w.line_edit_login.setFocus()
        if d.exec_() == QDialog.Accepted and w.status:
            return w.status
        return None
