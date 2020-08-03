# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtWidgets import QWidget, QDialog
from PyQt5.QtCore import pyqtSignal

from parsec.core.gui.trio_thread import ThreadSafeQtSignal
from parsec.core.gui.custom_dialogs import GreyedDialog, show_error
from parsec.core.gui.ui.init_organization_widget import Ui_InitOrganizationWidget
from parsec.core.gui.ui.init_workspace_widget import Ui_InitWorkspaceWidget
from parsec.core.gui.ui.init_users_widget import Ui_InitUsersWidget
from parsec.core.gui.jobs.workspace import _do_workspace_create, handle_create_workspace_errors
from parsec.core.gui.jobs.user import _do_invite_user, handle_invite_errors
from parsec.core.gui.lang import translate as _


class InitUsersWidget(QWidget, Ui_InitUsersWidget):
    invit_success = pyqtSignal()
    invit_error = pyqtSignal()

    def __init__(self, jobs_ctx, core, button_validate):
        super().__init__()
        self.setupUi(self)

        self.init_message.setText(_("TEXT_INIT_FIRST_WORKSPACE_SUCCESSED"))
        self.button_invit.setText(_("ACTION_USER_INVITE_DO_INVITE"))
        self.button_invit.clicked.connect(self._invit_user)

        self.email_edit_text.setPlaceholderText(_("TEXT_LABEL_USER_EMAIL_PLACEHOLDER"))
        self.email_edit_text.textChanged.connect(self._on_change_email)

        self.button_validate = button_validate
        self.button_validate.clicked.connect(self._close)
        self.dialog = None
        self.invit_status = None
        self._invit_job = None
        self.jobs_ctx = jobs_ctx
        self.core = core

        self.invit_success.connect(self._on_invit_success)
        self.invit_error.connect(self._on_invit_error)

    def _on_change_email(self):
        if self.email_edit_text.text():
            self.button_invit.setEnabled(True)
        else:
            self.button_invit.setEnabled(False)

    def _close(self):
        self.dialog.accept()

    def _on_invit_success(self):
        assert self._invit_job
        assert self._invit_job.is_finished()
        assert self._invit_job.status == "ok"

        self.email_edit_text.setText("")
        self.button_invit.setEnabled(False)
        self.button_validate.setEnabled(True)
        self.invit_status = self._invit_job.status
        self._invit_job = None

    def _on_invit_error(self):
        assert self._invit_job
        assert self._invit_job.is_finished()
        assert self._invit_job.status != "ok"
        self.button_validate.setEnabled(True)
        show_error(
            self, handle_invite_errors(self._invit_job.status), exception=self._invit_job.exc
        )

    def _invit_user(self):
        self.button_validate.setEnabled(False)
        self.button_invit.setEnabled(False)
        email = self.email_edit_text.text()
        if not email:
            return
        self._invit_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "invit_success"),
            ThreadSafeQtSignal(self, "invit_error"),
            _do_invite_user,
            core=self.core,
            email=email,
        )

    @property
    def req_job(self):
        return self._invit_job


class InitWorkspaceWidget(QWidget, Ui_InitWorkspaceWidget):
    create_success = pyqtSignal()
    create_error = pyqtSignal()

    def __init__(self, jobs_ctx, core, button_validate):
        super().__init__()
        self.setupUi(self)

        self.init_message.setText(_("TEXT_INIT_ORGANZATION_SUCCESSED"))
        self.name_message.setText(_("TEXT_WORKSPACE_NEW_INSTRUCTIONS"))

        self.name_edit_text.setPlaceholderText(_("TEXT_WORKSPACE_NEW_PLACEHOLDER"))
        self.name_edit_text.textChanged.connect(self._on_change_name)

        self.button_validate = button_validate
        self.button_validate.clicked.connect(self._create_workspace)

        self.jobs_ctx = jobs_ctx
        self.core = core
        self._create_job = None
        self.create_status = None
        self.create_success.connect(self._on_create_success)
        self.create_error.connect(self._on_create_error)

    def _on_create_success(self):
        assert self._create_job
        assert self._create_job.is_finished()
        assert self._create_job.status == "ok"

        self.create_status = self._create_job.status
        self._create_job = None

    def _on_create_error(self):
        assert self._create_job
        assert self._create_job.is_finished()
        assert self._create_job.status != "ok"

        job = self._create_job
        status = job.status
        self._create_job = None

        if status == "cancelled":
            return
        show_error(self, handle_create_workspace_errors(status), exception=job.exc)

    def _on_change_name(self):
        if self.name_edit_text.text():
            self.button_validate.setEnabled(True)
        else:
            self.button_validate.setEnabled(False)

    def _create_workspace(self):
        self.button_validate.setEnabled(False)
        name = self.name_edit_text.text()
        if not name:
            return
        self._create_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "create_success"),
            ThreadSafeQtSignal(self, "create_error"),
            _do_workspace_create,
            core=self.core,
            workspace_name=name,
        )

    @property
    def req_job(self):
        return self._create_job


class InitOrganizationWidget(QWidget, Ui_InitOrganizationWidget):
    def __init__(self, jobs_ctx, core, parent, **kwargs):
        super().__init__()
        self.setupUi(self)
        self.dialog = None
        self.current_widget = InitWorkspaceWidget(jobs_ctx, core, self.button_validate)
        self.current_widget.create_success.connect(self.on_workspace_created)
        self.jobs_ctx = jobs_ctx
        self.core = core
        self.parent = parent
        self.main_layout.addWidget(self.current_widget)
        self.button_validate.setEnabled(False)

    def _clear_current_widget(self):
        if not self.current_widget:
            return

        self.main_layout.removeWidget(self.current_widget)
        self.current_widget.hide()
        self.current_widget.setParent(None)
        self.current_widget = None

    def on_close(self):
        if self.current_widget.req_job:
            self.current_widget.req_job.cancel_and_join()

    def on_invit_successed(self):
        self.parent.reset()

    def on_workspace_created(self):
        self._clear_current_widget()
        self.current_widget = InitUsersWidget(self.jobs_ctx, self.core, self.button_validate)
        self.current_widget.invit_success.connect(self.on_invit_successed)
        self.current_widget.dialog = self.dialog
        self.main_layout.addWidget(self.current_widget)
        self.parent.parent.parent.menu.users_clicked.emit()
        self.parent = self.parent.parent.parent.users_widget
        self.dialog.label_title.setText(_("TEXT_FINALIZE_ORGANIZATION_SECOND_STEP_TITLE"))
        self.button_validate.setEnabled(True)
        self.button_validate.setText(_("ACTION_CLOSE"))

    @classmethod
    def exec_modal(cls, jobs_ctx, core, parent):
        w = cls(jobs_ctx, core, parent)
        d = GreyedDialog(
            w, _("TEXT_FINALIZE_ORGANIZATION_FIRST_STEP_TITLE"), parent=parent, width=1000
        )
        w.dialog = d
        if d.exec_() == QDialog.Accepted:
            return w
        return None
