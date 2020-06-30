# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import json
import os
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import Request, urlopen

import trio
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from structlog import get_logger

from parsec.core.gui.custom_dialogs import GreyedDialog, show_error
from parsec.core.gui.lang import translate as _
from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal
from parsec.core.gui.ui.create_org_first_page_widget import Ui_CreateOrgFirstPageWidget
from parsec.core.gui.ui.create_org_second_page_widget import Ui_CreateOrgSecondPageWidget
from parsec.core.gui.ui.create_org_widget import Ui_CreateOrgWidget
from parsec.core.types import BackendOrganizationBootstrapAddr

logger = get_logger()


async def _do_api_request(email, organization_id):
    data = json.dumps({"email": email, "organization_id": organization_id}).encode("utf-8")
    url = os.environ.get("BOOTSTRAP_API_URL", "https://bms.parsec.cloud/api/quickjoin")
    req = Request(url, method="POST", data=data, headers={"Content-Type": "application/json"})
    try:
        response = await trio.to_thread.run_sync(lambda: urlopen(req))
        if response.status != 200:
            raise JobResultError("invalid_response")
        try:
            content = await trio.to_thread.run_sync(lambda: response.read())
            content = json.loads(content)
            if content.get("error", None):
                raise JobResultError(content["error"])
            return (
                content["parsec_id"],
                BackendOrganizationBootstrapAddr.from_url(content["bootstrap_link"]),
            )
        except (TypeError, KeyError) as exc:
            raise JobResultError("invalid_response", exc=exc)
    except (HTTPException, URLError) as exc:
        raise JobResultError("offline", exc=exc)


class CreateOrgFirstPageWidget(QWidget, Ui_CreateOrgFirstPageWidget):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.radio_create_org.setChecked(True)


class CreateOrgSecondPageWidget(QWidget, Ui_CreateOrgSecondPageWidget):
    def __init__(self):
        super().__init__()
        self.setupUi(self)


class CreateOrgWidget(QWidget, Ui_CreateOrgWidget):
    req_success = pyqtSignal()
    req_error = pyqtSignal()

    def __init__(self, jobs_ctx):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.req_job = None
        self.dialog = None
        self.status = None
        self.button_validate.clicked.connect(self._validate_clicked)
        self.button_previous.clicked.connect(self._previous_clicked)
        self.button_previous.hide()
        self.current_widget = CreateOrgSecondPageWidget()
        self.main_layout.addWidget(self.current_widget)
        self.current_widget.line_edit_user_email.textChanged.connect(self._check_infos)
        self.current_widget.line_edit_org_name.textChanged.connect(self._check_infos)
        self.current_widget.check_accept_contract.clicked.connect(self._check_infos)
        self.button_validate.setEnabled(False)
        self.req_success.connect(self._on_req_success)
        self.req_error.connect(self._on_req_error)

    def _clear_page(self):
        item = self.main_layout.takeAt(0)
        if item:
            w = item.widget()
            self.main_layout.removeWidget(w)
            w.hide()
            w.setParent(None)

    def on_close(self):
        self.status = None
        if self.req_job:
            self.req_job.cancel_and_join()

    def _on_req_success(self):
        assert self.req_job
        assert self.req_job.is_finished()
        assert self.req_job.status == "ok"

        _, self.status = self.req_job.ret
        self.req_job = None
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when org wizard")

    def _on_req_error(self):
        assert self.req_job
        assert self.req_job.is_finished()
        assert self.req_job.status != "ok"

        status = self.req_job.status

        if status == "cancelled":
            return

        errmsg = None
        if status == "email_already_exists":
            errmsg = _("TEXT_ORG_WIZARD_EMAIL_ALREADY_EXISTS")
        elif status == "organization_already_exists":
            errmsg = _("TEXT_ORG_WIZARD_ORGANIZATION_ALREADY_EXISTS")
        elif status == "invalid_email":
            errmsg = _("TEXT_ORG_WIZARD_INVALID_EMAIL")
        elif status == "invalid_organization_id":
            errmsg = _("TEXT_ORG_WIZARD_INVALID_ORGANIZATION_ID")
        elif status == "invalid_response":
            errmsg = _("TEXT_ORG_WIZARD_INVALID_RESPONSE")
        elif status == "offline":
            errmsg = _("TEXT_ORG_WIZARD_OFFLINE")
        else:
            errmsg = _("TEXT_ORG_WIZARD_UNKNOWN_FAILURE")
        exc = self.req_job.exc
        if exc.params.get("exc"):
            exc = exc.params.get("exc")
        show_error(self, errmsg, exception=exc)
        self.req_job = None
        self.button_validate.setEnabled(True)
        self.button_previous.show()

    def _validate_clicked(self):
        if isinstance(self.current_widget, CreateOrgFirstPageWidget):
            if self.current_widget.radio_bootstrap_org.isChecked():
                self.status = ""
                if self.dialog:
                    self.dialog.accept()
                elif QApplication.activeModalWidget():
                    QApplication.activeModalWidget().accept()
                else:
                    logger.warning("Cannot close dialog when org wizard")
            else:
                self.button_validate.setText(_("ACTION_CREATE_ORGANIZATION"))
                self.button_previous.show()
                self._clear_page()
                self.current_widget = CreateOrgSecondPageWidget()
                self.main_layout.addWidget(self.current_widget)
                self.current_widget.line_edit_user_email.textChanged.connect(self._check_infos)
                self.current_widget.line_edit_org_name.textChanged.connect(self._check_infos)
                self.current_widget.check_accept_contract.clicked.connect(self._check_infos)
                self.button_validate.setEnabled(False)
        elif isinstance(self.current_widget, CreateOrgSecondPageWidget):
            self.req_job = self.jobs_ctx.submit_job(
                ThreadSafeQtSignal(self, "req_success"),
                ThreadSafeQtSignal(self, "req_error"),
                _do_api_request,
                email=self.current_widget.line_edit_user_email.text(),
                organization_id=self.current_widget.line_edit_org_name.text(),
            )
            self.button_validate.setEnabled(False)
            self.button_previous.hide()

    def _previous_clicked(self):
        self._clear_page()
        self.current_widget = CreateOrgFirstPageWidget()
        self.main_layout.addWidget(self.current_widget)
        self.button_previous.hide()
        self.button_validate.setText(_("ACTION_NEXT"))
        self.button_validate.setEnabled(True)

    def _check_infos(self):
        if (
            self.current_widget.line_edit_user_email.text()
            and self.current_widget.line_edit_org_name.text()
            and self.current_widget.check_accept_contract.isChecked()
        ):
            self.button_validate.setEnabled(True)
        else:
            self.button_validate.setEnabled(False)

    @classmethod
    def exec_modal(cls, jobs_ctx, parent):
        w = cls(jobs_ctx)
        d = GreyedDialog(w, _("TEXT_ORG_WIZARD_TITLE"), parent=parent)
        w.dialog = d
        if d.exec_() == QDialog.Accepted:
            return w.status
        return None
