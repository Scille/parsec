# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from collections import namedtuple

from PyQt5.QtCore import QCoreApplication, pyqtSignal, QEvent, QTimer
from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtWidgets import QWidget, QComboBox

from parsec.core.types import UserInfo
from parsec.core.fs import FSError, FSBackendOfflineError
from parsec.core.types import WorkspaceRole
from parsec.core.backend_connection import BackendNotAvailable

from parsec.core.gui.trio_thread import JobResultError, QtToTrioJob

from parsec.core.gui.custom_dialogs import show_error, GreyedDialog
from parsec.core.gui.custom_widgets import Pixmap
from parsec.core.gui.lang import translate as _
from parsec.core.gui.workspace_roles import get_role_translation, NOT_SHARED_KEY
from parsec.core.gui.ui.workspace_sharing_widget import Ui_WorkspaceSharingWidget
from parsec.core.gui.ui.sharing_widget import Ui_SharingWidget


_ROLES_TO_INDEX = {
    NOT_SHARED_KEY: 0,
    WorkspaceRole.READER: 1,
    WorkspaceRole.CONTRIBUTOR: 2,
    WorkspaceRole.MANAGER: 3,
    WorkspaceRole.OWNER: 4,
}


def _index_to_role(index):
    for role, idx in _ROLES_TO_INDEX.items():
        if index == idx:
            return role
    return None


async def _do_get_users(core, workspace_fs):
    ret = {}
    try:
        participants = await workspace_fs.get_user_roles()
        updated_participants = {}
        for user, role in participants.items():
            user_info = await core.get_user_info(user)
            updated_participants[user_info] = role
        # TODO: handle pagination
        users, _ = await core.find_humans()

        for user_info, role in updated_participants.items():
            ret[user_info] = role
        for user_info in users:
            if user_info not in ret:
                ret[user_info] = NOT_SHARED_KEY
        return ret
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc


async def _do_share_workspace(user_fs, workspace_fs, user_roles):
    errors = []
    successes = []
    workspace_name = workspace_fs.get_workspace_name()

    SharingResult = namedtuple("SharingResult", ("user_info", "status", "role", "error"))

    for user_info, role in user_roles.items():
        try:
            await user_fs.workspace_share(workspace_fs.workspace_id, user_info.user_id, role)
            successes.append(SharingResult(user_info, True, role, None))
        except ValueError:
            errors.append(SharingResult(user_info, False, role, "invalid-user"))
        except FSBackendOfflineError:
            errors.append(SharingResult(user_info, False, role, "offline"))
        except FSError:
            errors.append(SharingResult(user_info, False, role, "fs-error"))
        except Exception:
            errors.append(SharingResult(user_info, False, role, "error"))
    return workspace_name, successes, errors


class SharingWidget(QWidget, Ui_SharingWidget):
    role_changed = pyqtSignal(UserInfo, object)

    def __init__(self, user_info, is_current_user, current_user_role, role, enabled):
        super().__init__()
        self.setupUi(self)

        self.combo_role.installEventFilter(self)

        self.role = role
        self.current_user_role = current_user_role
        self.is_current_user = is_current_user
        self.user_info = user_info
        if self.is_current_user:
            self.label_name.setText(f"<b>{self.user_info.short_user_display}</b>")
        else:
            self.label_name.setText(self.user_info.short_user_display)
        if self.user_info.human_handle:
            self.label_email.setText(self.user_info.human_handle.email)

        if self.user_info.is_revoked:
            self.setDisabled(True)
            font = self.label_name.font()
            font.setStrikeOut(True)
            self.label_name.setFont(font)
            self.setToolTip(_("TEXT_WORKSPACE_SHARING_USER_HAS_BEEN_REVOKED"))

        if not enabled:
            for role, index in _ROLES_TO_INDEX.items():
                self.combo_role.insertItem(index, get_role_translation(role))
        else:
            current_index = _ROLES_TO_INDEX[self.current_user_role]
            for role, index in _ROLES_TO_INDEX.items():
                if current_index < index:
                    break
                self.combo_role.insertItem(index, get_role_translation(role))

        self.combo_role.setCurrentIndex(_ROLES_TO_INDEX[self.role])
        self.combo_role.currentIndexChanged.connect(self.on_role_changed)
        self.status_timer = QTimer()
        self.status_timer.setInterval(3000)
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(self._refresh_status)

    def _refresh_status(self):
        self.label_status.setPixmap(QPixmap())

    def set_status_updating(self):
        p = Pixmap(":/icons/images/material/update.svg")
        p.replace_color(QColor(0, 0, 0), QColor(0x99, 0x99, 0x99))
        self.label_status.setPixmap(p)

    def set_status_updated(self):
        p = Pixmap(":/icons/images/material/done.svg")
        p.replace_color(QColor(0, 0, 0), QColor(0x8B, 0xC3, 0x4A))
        self.label_status.setPixmap(p)
        self.status_timer.start()

    def set_status_update_failed(self):
        p = Pixmap(":/icons/images/material/sync_problem.svg")
        p.replace_color(QColor(0, 0, 0), QColor(0xF1, 0x96, 0x2B))
        self.label_status.setPixmap(p)
        self.status_timer.start()

    def on_role_changed(self, index):
        self.role_changed.emit(self.user_info, _index_to_role(index))

    def eventFilter(self, obj_src, event):
        if event.type() == QEvent.Wheel and isinstance(obj_src, QComboBox):
            event.ignore()
            return True
        return super().eventFilter(obj_src, event)

    @property
    def user_id(self):
        return self.user_info.user_id


class WorkspaceSharingWidget(QWidget, Ui_WorkspaceSharingWidget):
    get_users_success = pyqtSignal(QtToTrioJob)
    get_users_error = pyqtSignal(QtToTrioJob)
    share_success = pyqtSignal(QtToTrioJob)
    share_error = pyqtSignal(QtToTrioJob)
    closing = pyqtSignal(bool)

    def __init__(self, user_fs, workspace_fs, core, jobs_ctx):
        super().__init__()
        self.setupUi(self)
        self.user_fs = user_fs
        self.core = core
        self.jobs_ctx = jobs_ctx
        self.workspace_fs = workspace_fs

        self.has_changes = False

        self.share_success.connect(self._on_share_success)
        self.share_error.connect(self._on_share_error)
        self.get_users_success.connect(self._on_get_users_success)
        self.get_users_error.connect(self._on_get_users_error)
        self.line_edit_filter.textChanged.connect(self._on_filter_changed)

        ws_entry = self.jobs_ctx.run_sync(self.workspace_fs.get_workspace_entry)
        self.current_user_role = ws_entry.role
        self.reset()

    def _on_filter_changed(self, text):
        text = text.lower()
        for i in range(self.scroll_content.layout().count()):
            w = self.scroll_content.layout().itemAt(i).widget()
            if w:
                if text in w.user_info.short_user_display.lower():
                    w.setVisible(True)
                else:
                    w.setVisible(False)

    def add_participant(self, user_info, is_current_user, role):
        enabled = True
        if is_current_user:
            enabled = False
        elif (
            self.current_user_role == WorkspaceRole.READER
            or self.current_user_role == WorkspaceRole.CONTRIBUTOR
        ):
            enabled = False
        elif self.current_user_role == WorkspaceRole.MANAGER and (
            role == WorkspaceRole.OWNER or role == WorkspaceRole.MANAGER
        ):
            enabled = False

        w = SharingWidget(
            user_info=user_info,
            is_current_user=is_current_user,
            current_user_role=self.current_user_role,
            role=role,
            enabled=enabled,
        )
        w.role_changed.connect(self.on_role_changed)
        w.setEnabled(enabled)
        self.scroll_content.layout().insertWidget(self.scroll_content.layout().count() - 1, w)

    def _get_sharing_widget(self, user_id):
        for i in range(self.scroll_content.layout().count() - 1):
            item = self.scroll_content.layout().itemAt(i)
            if item and item.widget() and item.widget().user_id == user_id:
                return item.widget()
        return None

    def on_role_changed(self, user_info, role):
        if role == NOT_SHARED_KEY:
            role = None
        sharing_widget = self._get_sharing_widget(user_info.user_id)
        if sharing_widget:
            sharing_widget.set_status_updating()
        self.jobs_ctx.submit_job(
            self.share_success,
            self.share_error,
            _do_share_workspace,
            user_fs=self.user_fs,
            workspace_fs=self.workspace_fs,
            user_roles={user_info: role},
        )

    def _on_share_success(self, job):
        workspace_name, successes, errors = job.ret
        if successes:
            self.has_changes = True
        for result in successes:
            sharing_widget = self._get_sharing_widget(result.user_info.user_id)
            if sharing_widget:
                sharing_widget.set_status_updated()
        for result in errors:
            sharing_widget = self._get_sharing_widget(result.user_info.user_id)
            if sharing_widget:
                sharing_widget.set_status_update_failed()

        if errors:
            self._process_sharing_errors(workspace_name, errors)

    def _process_sharing_errors(self, workspace_name, errors):
        reset = True
        if errors:
            result = errors[0]
            if result.error == "offline":
                show_error(self, _("TEXT_WORKSPACE_SHARING_OFFLINE"))
                reset = False
            elif result.role == NOT_SHARED_KEY:
                show_error(
                    self,
                    _("TEXT_WORKSPACE_SHARING_UNSHARE_ERROR_workspace-user").format(
                        workspace=workspace_name, user=result.user_info.short_user_display
                    ),
                )
            else:
                show_error(
                    self,
                    _("TEXT_WORKSPACE_SHARING_SHARE_ERROR_workspace-user").format(
                        workspace=workspace_name, user=result.user_info.short_user_display
                    ),
                )
        if reset:
            self.reset()

    def _on_share_error(self, job):
        self.reset()

    def _on_get_users_success(self, job):
        users = job.ret
        while self.scroll_content.layout().count() > 1:
            item = self.scroll_content.layout().takeAt(0)
            w = item.widget()
            self.scroll_content.layout().removeItem(item)
            w.setParent(None)
        QCoreApplication.processEvents()
        for user_info, role in users.items():
            if not user_info.revoked_on:
                self.add_participant(
                    user_info,
                    is_current_user=user_info.user_id == self.core.device.user_id,
                    role=role or "NOT_SHARED",
                )
        self.spinner.hide()
        self.widget_users.show()

    def _on_get_users_error(self, job):
        assert job.is_finished()
        assert job.status != "ok"

        if job.status == "offline":
            show_error(self, _("TEXT_WORKSPACE_SHARING_OFFLINE"))
        self.spinner.hide()
        self.widget_users.show()

    def on_close(self):
        self.closing.emit(self.has_changes)

    def reset(self):
        self.spinner.show()
        self.widget_users.hide()
        self.jobs_ctx.submit_job(
            self.get_users_success,
            self.get_users_error,
            _do_get_users,
            core=self.core,
            workspace_fs=self.workspace_fs,
        )

    @classmethod
    def show_modal(cls, user_fs, workspace_fs, core, jobs_ctx, parent, on_finished):
        workspace_name = jobs_ctx.run_sync(workspace_fs.get_workspace_name)

        w = cls(user_fs=user_fs, workspace_fs=workspace_fs, core=core, jobs_ctx=jobs_ctx)
        d = GreyedDialog(
            w,
            title=_("TEXT_WORKSPACE_SHARING_TITLE_workspace").format(workspace=workspace_name),
            parent=parent,
            width=1000,
        )
        d.closing.connect(w.on_close)
        w.closing.connect(on_finished)
        # Unlike exec_, show is asynchronous and works within the main Qt loop
        d.show()
        return w
