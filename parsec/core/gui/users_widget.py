# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QGraphicsDropShadowEffect, QMenu, QWidget

from parsec.api.data import UserProfile
from parsec.core.backend_connection import BackendConnectionError, BackendNotAvailable
from parsec.core.gui.custom_dialogs import ask_question, show_error, show_info
from parsec.core.gui.invite_user_widget import InviteUserWidget
from parsec.core.gui.lang import format_datetime
from parsec.core.gui.lang import translate as _
from parsec.core.gui.trio_thread import JobResultError, QtToTrioJob, ThreadSafeQtSignal
from parsec.core.gui.ui.user_button import Ui_UserButton
from parsec.core.gui.ui.users_widget import Ui_UsersWidget


class UserButton(QWidget, Ui_UserButton):
    revoke_clicked = pyqtSignal(QWidget)

    def __init__(
        self,
        user_id,
        user_display,
        is_current_user,
        is_admin,
        certified_on,
        is_revoked,
        current_user_is_admin,
    ):
        super().__init__()
        self.setupUi(self)
        self.current_user_is_admin = current_user_is_admin
        self.is_admin = is_admin
        self.is_revoked = is_revoked
        self._is_revoked = is_revoked
        self.certified_on = certified_on
        self.is_current_user = is_current_user
        self.user_id = user_id
        self.user_display = user_display
        self.label_username.setText(user_display)
        self.user_icon.apply_style()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.label_created_on.setText(format_datetime(self.certified_on, full=True))
        self.label_role.setText(
            _("TEXT_USER_ROLE_ADMIN") if self.is_admin else _("TEXT_USER_ROLE_CONTRIBUTOR")
        )
        if self.is_current_user:
            self.label_user_is_current.setText("({})".format(_("TEXT_USER_IS_CURRENT")))
        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(0x99, 0x99, 0x99))
        effect.setBlurRadius(10)
        effect.setXOffset(2)
        effect.setYOffset(2)
        self.setGraphicsEffect(effect)

    @property
    def is_revoked(self):
        return self._is_revoked

    @is_revoked.setter
    def is_revoked(self, value):
        self._is_revoked = value
        if value:
            self.label_revoked.setText(_("TEXT_USER_IS_REVOKED"))
            self.setStyleSheet(
                "#UserButton, #widget { background-color: #E3E3E3; border-radius: 4px; }"
            )
        else:
            self.label_revoked.setText("")
            self.setStyleSheet(
                "#UserButton, #widget { background-color: #FFFFFF; border-radius: 4px; }"
            )

    def show_context_menu(self, pos):
        if self.is_revoked or self.is_current_user or not self.current_user_is_admin:
            return
        global_pos = self.mapToGlobal(pos)
        menu = QMenu(self)
        action = menu.addAction(_("ACTION_USER_MENU_REVOKE"))
        action.triggered.connect(self.revoke)
        menu.exec_(global_pos)

    def revoke(self):
        self.revoke_clicked.emit(self)


async def _do_revoke_user(core, user_id, button):
    try:
        await core.revoke_user(user_id)
        return button
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    except BackendConnectionError as exc:
        raise JobResultError("error") from exc


async def _do_list_users(core):
    try:
        users, total = await core.find_humans()
        # TODO: handle pagination ! (currently we only display the first 100 users...)
        return users
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    except BackendConnectionError as exc:
        raise JobResultError("error") from exc


class UsersWidget(QWidget, Ui_UsersWidget):
    revoke_success = pyqtSignal(QtToTrioJob)
    revoke_error = pyqtSignal(QtToTrioJob)
    list_success = pyqtSignal(QtToTrioJob)
    list_error = pyqtSignal(QtToTrioJob)

    def __init__(self, core, jobs_ctx, event_bus, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setupUi(self)
        self.core = core
        self.jobs_ctx = jobs_ctx
        self.event_bus = event_bus
        self.users = []
        self.button_add_user.apply_style()
        if core.device.is_admin:
            self.button_add_user.clicked.connect(self.invite_user)
        else:
            self.button_add_user.hide()
        self.filter_timer = QTimer()
        self.filter_timer.setInterval(300)
        self.line_edit_search.textChanged.connect(self.filter_timer.start)
        self.filter_timer.timeout.connect(self.on_filter_timer_timeout)
        self.revoke_success.connect(self.on_revoke_success)
        self.revoke_error.connect(self.on_revoke_error)
        self.list_success.connect(self.on_list_success)
        self.list_error.connect(self.on_list_error)
        self.reset()

    def on_filter_timer_timeout(self):
        self.filter_users(self.line_edit_search.text())

    def filter_users(self, pattern):
        pattern = pattern.lower()
        for i in range(self.layout_users.count()):
            item = self.layout_users.itemAt(i)
            if item:
                w = item.widget()
                if pattern and pattern not in w.user_name.lower():
                    w.hide()
                else:
                    w.show()

    def invite_user(self):
        InviteUserWidget.exec_modal(core=self.core, jobs_ctx=self.jobs_ctx, parent=self)
        self.reset()

    def add_user(self, user_id, user_display, is_current_user, is_admin, certified_on, is_revoked):
        if user_id in self.users:
            return
        button = UserButton(
            user_id,
            user_display,
            is_current_user,
            is_admin,
            certified_on,
            is_revoked,
            current_user_is_admin=self.core.device.is_admin,
        )
        self.layout_users.addWidget(button)
        button.revoke_clicked.connect(self.revoke_user)
        button.show()
        self.users.append(user_id)

    def on_revoke_success(self, job):
        button = job.ret
        show_info(self, _("TEXT_USER_REVOKE_SUCCESS_user").format(user=button.user_display))
        button.is_revoked = True

    def on_revoke_error(self, job):
        status = job.status
        if status == "already_revoked":
            errmsg = _("TEXT_USER_REVOCATION_USER_ALREADY_REVOKED")
        elif status == "not_found":
            errmsg = _("TEXT_USER_REVOCATION_USER_NOT_FOUND")
        elif status == "not_allowed":
            errmsg = _("TEXT_USER_REVOCATION_NOT_ENOUGH_PERMISSIONS")
        elif status == "offline":
            errmsg = _("TEXT_USER_REVOCATION_BACKEND_OFFLINE")
        else:
            errmsg = _("TEXT_USER_REVOCATION_UNKNOWN_FAILURE")
        show_error(self, errmsg, exception=job.exc)

    def revoke_user(self, user_button):
        result = ask_question(
            self,
            _("TEXT_USER_REVOCATION_TITLE"),
            _("TEXT_USER_REVOCATION_INSTRUCTIONS_user").format(user=user_button.user_display),
            [_("ACTION_USER_REVOCATION_CONFIRM"), _("ACTION_CANCEL")],
        )
        if result != _("ACTION_USER_REVOCATION_CONFIRM"):
            return
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "revoke_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "revoke_error", QtToTrioJob),
            _do_revoke_user,
            core=self.core,
            user_id=user_button.user_id,
            button=user_button,
        )

    def on_list_success(self, job):
        self.users = []

        while self.layout_users.count() != 0:
            item = self.layout_users.takeAt(0)
            if item:
                w = item.widget()
                self.layout_users.removeWidget(w)
                w.hide()
                w.setParent(None)

        current_user = self.core.device.user_id
        for user in job.ret:
            self.add_user(
                user_id=user.user_id,
                user_display=user.user_display,
                is_current_user=current_user == user.user_id,
                is_admin=user.profile == UserProfile.ADMIN,
                certified_on=user.created_on,
                is_revoked=user.is_revoked,
            )

    def on_list_error(self, job):
        status = job.status
        if status == "offline":
            return
        else:
            errmsg = _("TEXT_USER_LIST_RETRIEVABLE_FAILURE")
        show_error(self, errmsg, exception=job.exc)

    def reset(self):
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "list_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "list_error", QtToTrioJob),
            _do_list_users,
            core=self.core,
        )
