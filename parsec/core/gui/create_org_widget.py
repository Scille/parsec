# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from structlog import get_logger

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QApplication, QDialog

from parsec.api.protocol import OrganizationID, DeviceName, HumanHandle
from parsec.core.backend_connection import (
    apiv1_backend_anonymous_cmds_factory,
    BackendConnectionRefused,
    BackendNotAvailable,
)
from parsec.core.types import BackendOrganizationBootstrapAddr, BackendAddr
from parsec.core.invite import (
    bootstrap_organization,
    InviteNotFoundError,
    InviteAlreadyUsedError,
    InviteTimestampError,
    InviteError,
)
from parsec.core.local_device import save_device_with_password

from parsec.core.gui.custom_dialogs import GreyedDialog, show_error, show_info
from parsec.core.gui.trio_thread import JobResultError
from parsec.core.gui.desktop import get_default_device
from parsec.core.gui.lang import translate as _
from parsec.core.gui import validators

from parsec.core.gui.ui.create_org_widget import Ui_CreateOrgWidget
from parsec.core.gui.ui.create_org_user_info_widget import Ui_CreateOrgUserInfoWidget
from parsec.core.gui.ui.create_org_device_info_widget import Ui_CreateOrgDeviceInfoWidget


logger = get_logger()


async def _do_create_org(config, human_handle, device_name, password, backend_addr):
    try:
        async with apiv1_backend_anonymous_cmds_factory(addr=backend_addr) as cmds:
            new_device = await bootstrap_organization(
                cmds=cmds, human_handle=human_handle, device_label=device_name
            )
            save_device_with_password(
                config_dir=config.config_dir, device=new_device, password=password
            )
            return new_device, password
    except InviteNotFoundError as exc:
        raise JobResultError("invite-not-found", exc=exc)
    except InviteAlreadyUsedError as exc:
        raise JobResultError("invite-already-used", exc=exc)
    except BackendConnectionRefused as exc:
        raise JobResultError("connection-refused", exc=exc)
    except BackendNotAvailable as exc:
        raise JobResultError("connection-error", exc=exc)
    except InviteTimestampError as exc:
        raise JobResultError("timestamp-error", exc=exc)
    except InviteError as exc:
        raise JobResultError("invite-error", exc=exc)


class CreateOrgUserInfoWidget(QWidget, Ui_CreateOrgUserInfoWidget):
    valid_info_entered = pyqtSignal()
    invalid_info_entered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.line_edit_user_email.validity_changed.connect(self.check_infos)
        self.line_edit_user_email.set_validator(validators.EmailValidator())
        self.line_edit_user_full_name.textChanged.connect(self.check_infos)
        self.line_edit_org_name.validity_changed.connect(self.check_infos)
        self.line_edit_org_name.set_validator(validators.OrganizationIDValidator())
        self.line_edit_backend_addr.set_validator(validators.BackendAddrValidator())
        self.line_edit_backend_addr.validity_changed.connect(self.check_infos)
        self.check_accept_contract.clicked.connect(self.check_infos)
        self.radio_use_commercial.toggled.connect(self._switch_server)
        self.radio_use_custom.toggled.connect(self._switch_server)
        self.radio_use_commercial.setChecked(True)

    def _are_inputs_valid(self):
        return (
            self.line_edit_user_email.is_input_valid()
            and self.line_edit_user_full_name.text()
            and self.line_edit_org_name.is_input_valid()
            and (
                (self.radio_use_commercial.isChecked() and self.check_accept_contract.isChecked())
                or (
                    self.radio_use_custom.isChecked()
                    and self.line_edit_backend_addr.is_input_valid()
                )
            )
        )

    def _switch_server(self, _):
        if self.radio_use_commercial.isChecked():
            self.widget_custom_backend.hide()
            self.widget_contract.show()
        else:
            self.widget_contract.hide()
            self.widget_custom_backend.show()
        self.check_infos()

    def check_infos(self, _=None):
        if self._are_inputs_valid():
            self.valid_info_entered.emit()
        else:
            self.invalid_info_entered.emit()

    @property
    def backend_addr(self):
        return (
            BackendAddr.from_url(self.line_edit_backend_addr.text(), allow_http_redirection=True)
            if self.radio_use_custom.isChecked()
            else None
        )


class CreateOrgDeviceInfoWidget(QWidget, Ui_CreateOrgDeviceInfoWidget):
    valid_info_entered = pyqtSignal()
    invalid_info_entered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.widget_password.info_changed.connect(self.check_infos)
        self.line_edit_device.setText(get_default_device())
        self.line_edit_device.validity_changed.connect(self.check_infos)
        self.line_edit_device.set_validator(validators.DeviceNameValidator())

    def check_infos(self, _=None):
        if self.line_edit_device.is_input_valid() and self.widget_password.is_valid():
            self.valid_info_entered.emit()
        else:
            self.invalid_info_entered.emit()

    def set_excluded_strings(self, excluded_strings):
        self.widget_password.excluded_strings = excluded_strings

    @property
    def password(self):
        return self.widget_password.password


class CreateOrgWidget(QWidget, Ui_CreateOrgWidget):
    req_success = pyqtSignal()
    req_error = pyqtSignal()

    def __init__(self, jobs_ctx, config, start_addr):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.config = config
        self.create_job = None
        self.dialog = None
        self.status = None

        self.device_widget = CreateOrgDeviceInfoWidget()
        self.device_widget.valid_info_entered.connect(self._on_info_valid)
        self.device_widget.invalid_info_entered.connect(self._on_info_invalid)

        self.user_widget = CreateOrgUserInfoWidget()
        self.user_widget.valid_info_entered.connect(self._on_info_valid)
        self.user_widget.invalid_info_entered.connect(self._on_info_invalid)

        self.main_layout.addWidget(self.user_widget)
        self.main_layout.addWidget(self.device_widget)

        self._on_previous_clicked()

        self.button_validate.setEnabled(False)
        self.button_previous.clicked.connect(self._on_previous_clicked)
        self.button_previous.hide()

        self.req_success.connect(self._on_req_success)
        self.req_error.connect(self._on_req_error)

        self.start_addr = start_addr

        if self.start_addr:
            self.user_widget.line_edit_org_name.setText(self.start_addr.organization_id)
            self.user_widget.line_edit_org_name.setReadOnly(True)
            self.label_instructions.setText(
                _("TEXT_BOOTSTRAP_ORGANIZATION_INSTRUCTIONS_organization").format(
                    organization=self.start_addr.organization_id
                )
            )
            # Not creating on the default server
            if (
                self.start_addr.hostname != config.preferred_org_creation_backend_addr.hostname
                or self.start_addr.port != config.preferred_org_creation_backend_addr.port
            ):
                self.user_widget.radio_use_custom.setChecked(True)
                self.user_widget.radio_use_commercial.setDisabled(True)
                # Will not be used, it just makes the display prettier
                backend_addr = BackendAddr(
                    self.start_addr.hostname, self.start_addr.port, self.start_addr.use_ssl
                )
                self.user_widget.line_edit_backend_addr.setText(backend_addr.to_url())
                self.user_widget.line_edit_backend_addr.setDisabled(True)
                self.user_widget.line_edit_backend_addr.setCursorPosition(0)
            else:
                self.user_widget.radio_use_commercial.setChecked(True)
                self.user_widget.radio_use_custom.setDisabled(True)

    def _on_info_valid(self):
        self.button_validate.setEnabled(True)

    def _on_info_invalid(self):
        self.button_validate.setEnabled(False)

    def on_close(self):
        self.status = None
        if self.create_job:
            self.create_job.cancel_and_join()

    def _on_previous_clicked(self):
        self.user_widget.show()
        self.device_widget.hide()

        try:
            self.button_validate.clicked.disconnect(self._on_validate_clicked)
        except TypeError:
            pass
        self.button_validate.setText(_("ACTION_NEXT"))
        self.button_validate.clicked.connect(self._on_next_clicked)
        self.button_previous.hide()
        self.user_widget.check_infos()

    def _on_next_clicked(self):
        self.user_widget.hide()
        self.device_widget.show()
        self.device_widget.set_excluded_strings(
            [
                self.user_widget.line_edit_org_name.text(),
                self.user_widget.line_edit_user_full_name.text(),
                self.user_widget.line_edit_user_email.text(),
            ]
        )

        try:
            self.button_validate.clicked.disconnect(self._on_next_clicked)
        except TypeError:
            pass
        self.button_validate.setText(_("ACTION_CREATE_ORG"))
        self.button_validate.clicked.connect(self._on_validate_clicked)
        self.button_previous.show()
        self.device_widget.check_infos()

    def _on_validate_clicked(self):
        backend_addr = None
        org_id = None
        device_name = None
        human_handle = None

        if self.start_addr:
            backend_addr = self.start_addr
        else:
            try:
                org_id = OrganizationID(self.user_widget.line_edit_org_name.text())
            except ValueError as exc:
                show_error(self, _("TEXT_ORG_WIZARD_INVALID_ORGANIZATION_ID"), exception=exc)
                return
            try:
                backend_addr = BackendOrganizationBootstrapAddr.build(
                    backend_addr=self.user_widget.backend_addr
                    if self.user_widget.radio_use_custom.isChecked()
                    else self.config.preferred_org_creation_backend_addr,
                    organization_id=org_id,
                )
            except ValueError as exc:
                show_error(self, _("TEXT_ORG_WIZARD_INVALID_BACKEND_ADDR"), exception=exc)
                return
        try:
            device_name = DeviceName(self.device_widget.line_edit_device.text())
        except ValueError as exc:
            show_error(self, _("TEXT_ORG_WIZARD_INVALID_DEVICE_NAME"), exception=exc)
            return
        try:
            user_name = validators.trim_user_name(self.user_widget.line_edit_user_full_name.text())

            human_handle = HumanHandle(self.user_widget.line_edit_user_email.text(), user_name)
        except ValueError as exc:
            show_error(self, _("TEXT_ORG_WIZARD_INVALID_HUMAN_HANDLE"), exception=exc)
            return

        self.create_job = self.jobs_ctx.submit_job(
            self.req_success,
            self.req_error,
            _do_create_org,
            config=self.config,
            human_handle=human_handle,
            device_name=device_name,
            password=self.device_widget.password,
            backend_addr=backend_addr,
        )
        self.button_validate.setEnabled(False)

    def _on_req_success(self):
        assert self.create_job
        assert self.create_job.is_finished()
        assert self.create_job.status == "ok"

        self.status = self.create_job.ret
        self.create_job = None
        show_info(
            parent=self,
            message=_("TEXT_BOOTSTRAP_ORG_SUCCESS_organization").format(
                organization=self.status[0].organization_id
            ),
            button_text=_("ACTION_CONTINUE"),
        )
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when org wizard")

    def _on_req_error(self):
        assert self.create_job
        assert self.create_job.is_finished()
        assert self.create_job.status != "ok"

        status = self.create_job.status

        if status == "cancelled":
            return

        errmsg = None
        if status == "organization_already_exists":
            errmsg = _("TEXT_ORG_WIZARD_ORGANIZATION_ALREADY_EXISTS")
        elif status == "invalid_email":
            errmsg = _("TEXT_ORG_WIZARD_INVALID_EMAIL")
        elif status == "invalid_organization_id":
            errmsg = _("TEXT_ORG_WIZARD_INVALID_ORGANIZATION_ID")
        elif status == "offline":
            errmsg = _("TEXT_ORG_WIZARD_OFFLINE")
        elif status == "invite-not-found":
            errmsg = _("TEXT_ORG_WIZARD_INVITE_NOT_FOUND")
        elif status == "invite-already-used":
            if self.start_addr:
                errmsg = _("TEXT_ORG_WIZARD_INVITE_ALREADY_USED")
            else:
                errmsg = _("TEXT_ORG_WIZARD_ORGANIZATION_ALREADY_EXISTS")
        elif status == "connection-refused":
            errmsg = _("TEXT_ORG_WIZARD_CONNECTION_REFUSED")
        elif status == "connection-error":
            errmsg = _("TEXT_ORG_WIZARD_CONNECTION_ERROR")
        elif status == "timestamp-error":
            errmsg = _("TEXT_ORG_WIZARD_INVALID_TIMESTAMP")
        else:
            errmsg = _("TEXT_ORG_WIZARD_UNKNOWN_FAILURE")
        exc = self.create_job.exc
        if exc.params.get("exc"):
            exc = exc.params.get("exc")
        show_error(self, errmsg, exception=exc)
        self.status = None
        self.create_job = None
        self.button_validate.setEnabled(True)

    @classmethod
    def show_modal(cls, jobs_ctx, config, parent, on_finished, start_addr=None):
        w = cls(jobs_ctx, config, start_addr)
        d = GreyedDialog(w, _("TEXT_ORG_WIZARD_TITLE"), parent=parent, width=1000)
        w.dialog = d

        def _on_finished(result):
            if result == QDialog.Accepted:
                return on_finished(w.status)
            return on_finished(None)

        d.finished.connect(_on_finished)
        # Unlike exec_, show is asynchronous and works within the main Qt loop
        d.show()
        return w
