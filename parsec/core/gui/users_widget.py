# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


from uuid import UUID

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import QWidget, QMenu, QGraphicsDropShadowEffect, QLabel
from PyQt5.QtGui import QColor

from parsec.api.protocol import InvitationType
from parsec.api.data import UserProfile
from parsec.core.types import BackendInvitationAddr, UserInfo

from parsec.core.gui.trio_thread import ThreadSafeQtSignal, QtToTrioJob
from parsec.core.gui.custom_dialogs import show_error, show_info, ask_question, get_text_input
from parsec.core.gui.custom_widgets import ensure_string_size
from parsec.core.gui.flow_layout import FlowLayout
from parsec.core.gui import desktop
from parsec.core.gui.lang import translate as _
from parsec.core.gui.greet_user_widget import GreetUserWidget
from parsec.core.gui.ui.user_button import Ui_UserButton
from parsec.core.gui.ui.user_invitation_button import Ui_UserInvitationButton
from parsec.core.gui.ui.users_widget import Ui_UsersWidget
from parsec.core.gui.jobs.user import (
    _do_revoke_user,
    _do_list_users_and_invitations,
    _do_cancel_invitation,
    _do_invite_user,
    handle_invite_errors,
)


class UserInvitationButton(QWidget, Ui_UserInvitationButton):
    greet_clicked = pyqtSignal(UUID)
    cancel_clicked = pyqtSignal(UUID)

    def __init__(self, email, addr):
        super().__init__()
        self.setupUi(self)
        self.addr = addr
        self.email = email
        self.label_addr.setText(ensure_string_size(str(self.addr), 260, self.label_addr.font()))
        self.label_addr.setToolTip(str(self.addr))
        self.label_email.setText(ensure_string_size(self.email, 260, self.label_email.font()))
        self.label_email.setToolTip(self.email)

        self.button_greet.clicked.connect(self._on_greet_clicked)
        self.button_cancel.clicked.connect(self._on_cancel_clicked)
        self.button_cancel.apply_style()
        self.label_icon.apply_style()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(0x99, 0x99, 0x99))
        effect.setBlurRadius(10)
        effect.setXOffset(2)
        effect.setYOffset(2)
        self.setGraphicsEffect(effect)

    def show_context_menu(self, pos):
        global_pos = self.mapToGlobal(pos)
        menu = QMenu(self)
        action = menu.addAction(_("ACTION_USER_INVITE_COPY_ADDR"))
        action.triggered.connect(self.copy_addr)
        action = menu.addAction(_("ACTION_USER_INVITE_COPY_EMAIL"))
        action.triggered.connect(self.copy_email)
        menu.exec_(global_pos)

    def copy_addr(self):
        desktop.copy_to_clipboard(str(self.addr))

    def copy_email(self):
        desktop.copy_to_clipboard(self.email)

    @property
    def token(self):
        return self.addr.token

    def _on_greet_clicked(self):
        self.greet_clicked.emit(self.token)

    def _on_cancel_clicked(self):
        self.cancel_clicked.emit(self.token)


class UserButton(QWidget, Ui_UserButton):
    revoke_clicked = pyqtSignal(UserInfo)

    def __init__(self, user_info, is_current_user, current_user_is_admin):
        super().__init__()
        self.setupUi(self)

        self.user_info = user_info
        self.is_current_user = is_current_user
        self.current_user_is_admin = current_user_is_admin

        if self.is_current_user:
            self.label_is_current.setText("({})".format(_("TEXT_USER_IS_CURRENT")))
        self.label_icon.apply_style()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(0x99, 0x99, 0x99))
        effect.setBlurRadius(10)
        effect.setXOffset(2)
        effect.setYOffset(2)
        self.setGraphicsEffect(effect)

    @property
    def user_info(self):
        return self._user_info

    @user_info.setter
    def user_info(self, val):
        profiles_txt = {
            UserProfile.OUTSIDER: _("TEXT_USER_PROFILE_OUTSIDER"),
            UserProfile.STANDARD: _("TEXT_USER_PROFILE_STANDARD"),
            UserProfile.ADMIN: _("TEXT_USER_PROFILE_ADMIN"),
        }

        self._user_info = val
        if self.user_info.is_revoked:
            self.setToolTip(_("TEXT_USER_IS_REVOKED"))
            self.widget.setStyleSheet("background-color: #DDDDDD;")
        else:
            self.setToolTip("")
            self.widget.setStyleSheet("background-color: #FFFFFF;")
        if self.user_info.human_handle:
            self.label_email.setText(
                ensure_string_size(self.user_info.human_handle.email, 260, self.label_email.font())
            )
            self.label_email.setToolTip(self.user_info.human_handle.email)

        self.label_username.setText(
            ensure_string_size(self.user_info.short_user_display, 260, self.label_username.font())
        )
        self.label_username.setToolTip(self.user_info.short_user_display)
        self.label_role.setText(profiles_txt[self.user_info.profile])

    def show_context_menu(self, pos):
        if self.user_info.is_revoked or self.is_current_user or not self.current_user_is_admin:
            return
        global_pos = self.mapToGlobal(pos)
        menu = QMenu(self)
        action = menu.addAction(_("ACTION_USER_MENU_REVOKE"))
        action.triggered.connect(self.revoke)
        menu.exec_(global_pos)

    def revoke(self):
        self.revoke_clicked.emit(self.user_info)


class UsersWidget(QWidget, Ui_UsersWidget):
    revoke_success = pyqtSignal(QtToTrioJob)
    revoke_error = pyqtSignal(QtToTrioJob)
    list_success = pyqtSignal(QtToTrioJob)
    list_error = pyqtSignal(QtToTrioJob)
    invite_user_success = pyqtSignal(QtToTrioJob)
    invite_user_error = pyqtSignal(QtToTrioJob)
    cancel_invitation_success = pyqtSignal(QtToTrioJob)
    cancel_invitation_error = pyqtSignal(QtToTrioJob)

    def __init__(self, core, jobs_ctx, event_bus, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setupUi(self)
        self.core = core
        self.jobs_ctx = jobs_ctx
        self.event_bus = event_bus
        self.layout_users = FlowLayout(spacing=40)
        self.layout_content.addLayout(self.layout_users)
        self.button_add_user.apply_style()
        if core.device.is_admin:
            self.button_add_user.clicked.connect(self.invite_user)
        else:
            self.button_add_user.hide()
        self.filter_timer = QTimer()
        self.filter_timer.setInterval(300)
        self.line_edit_search.textChanged.connect(self.filter_timer.start)
        self.filter_timer.timeout.connect(self.on_filter_timer_timeout)
        self.revoke_success.connect(self._on_revoke_success)
        self.revoke_error.connect(self._on_revoke_error)
        self.list_success.connect(self._on_list_success)
        self.list_error.connect(self._on_list_error)
        self.invite_user_success.connect(self._on_invite_user_success)
        self.invite_user_error.connect(self._on_invite_user_error)
        self.cancel_invitation_success.connect(self._on_cancel_invitation_success)
        self.cancel_invitation_error.connect(self._on_cancel_invitation_error)

    def show(self):
        self.reset()
        super().show()

    def on_filter_timer_timeout(self):
        self.filter_users(self.line_edit_search.text())

    def filter_users(self, pattern):
        pattern = pattern.lower()
        for i in range(self.layout_users.count()):
            item = self.layout_users.itemAt(i)
            if item:
                w = item.widget()
                if pattern and (
                    isinstance(w, UserButton)
                    and pattern not in w.user_info.user_display.lower()
                    or isinstance(w, UserInvitationButton)
                    and pattern not in w.email.lower()
                ):
                    w.hide()
                else:
                    w.show()

    def invite_user(self):
        user_email = get_text_input(
            self,
            _("TEXT_USER_INVITE_EMAIL"),
            _("TEXT_USER_INVITE_EMAIL_INSTRUCTIONS"),
            placeholder=_("TEXT_USER_INVITE_EMAIL_PLACEHOLDER"),
            button_text=_("ACTION_USER_INVITE_DO_INVITE"),
        )
        if not user_email:
            return

        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "invite_user_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "invite_user_error", QtToTrioJob),
            _do_invite_user,
            core=self.core,
            email=user_email,
        )

    def add_user(self, user_info, is_current_user):
        button = UserButton(
            user_info=user_info,
            is_current_user=is_current_user,
            current_user_is_admin=self.core.device.is_admin,
        )
        self.layout_users.addWidget(button)
        button.revoke_clicked.connect(self.revoke_user)
        button.show()

    def add_user_invitation(self, email, invite_addr):
        button = UserInvitationButton(email, invite_addr)
        self.layout_users.addWidget(button)
        button.greet_clicked.connect(self.greet_user)
        button.cancel_clicked.connect(self.cancel_invitation)
        button.show()

    def greet_user(self, token):
        GreetUserWidget.show_modal(
            core=self.core, jobs_ctx=self.jobs_ctx, token=token, parent=self, on_finished=self.reset
        )

    def cancel_invitation(self, token):
        r = ask_question(
            self,
            _("TEXT_USER_INVITE_CANCEL_INVITE_QUESTION_TITLE"),
            _("TEXT_USER_INVITE_CANCEL_INVITE_QUESTION_CONTENT"),
            [_("TEXT_USER_INVITE_CANCEL_INVITE_ACCEPT"), _("ACTION_NO")],
        )
        if r != _("TEXT_USER_INVITE_CANCEL_INVITE_ACCEPT"):
            return
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "cancel_invitation_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "cancel_invitation_error", QtToTrioJob),
            _do_cancel_invitation,
            core=self.core,
            token=token,
        )

    def _on_revoke_success(self, job):
        assert job.is_finished()
        assert job.status == "ok"

        user_info = job.ret
        show_info(
            self, _("TEXT_USER_REVOKE_SUCCESS_user").format(user=user_info.short_user_display)
        )
        for i in range(self.layout_users.count()):
            item = self.layout_users.itemAt(i)
            if item:
                button = item.widget()
                if (
                    button
                    and isinstance(button, UserButton)
                    and button.user_info.user_id == user_info.user_id
                ):
                    button.user_info = user_info

    def _on_revoke_error(self, job):
        assert job.is_finished()
        assert job.status != "ok"

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

    def revoke_user(self, user_info):
        result = ask_question(
            self,
            _("TEXT_USER_REVOCATION_TITLE"),
            _("TEXT_USER_REVOCATION_INSTRUCTIONS_user").format(user=user_info.short_user_display),
            [_("ACTION_USER_REVOCATION_CONFIRM"), _("ACTION_CANCEL")],
        )
        if result != _("ACTION_USER_REVOCATION_CONFIRM"):
            return
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "revoke_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "revoke_error", QtToTrioJob),
            _do_revoke_user,
            core=self.core,
            user_info=user_info,
        )

    def _flush_users_list(self):
        self.users = []
        while self.layout_users.count() != 0:
            item = self.layout_users.takeAt(0)
            if item:
                w = item.widget()
                self.layout_users.removeWidget(w)
                w.hide()
                w.setParent(None)

    def _on_list_success(self, job):
        assert job.is_finished()
        assert job.status == "ok"

        users, invitations = job.ret
        self._flush_users_list()

        current_user = self.core.device.user_id
        for user_info in users:
            self.add_user(user_info=user_info, is_current_user=current_user == user_info.user_id)

        for invitation in invitations:
            addr = BackendInvitationAddr.build(
                backend_addr=self.core.device.organization_addr,
                organization_id=self.core.device.organization_id,
                invitation_type=InvitationType.USER,
                token=invitation["token"],
            )
            self.add_user_invitation(invitation["claimer_email"], addr)

    def _on_list_error(self, job):
        assert job.is_finished()
        assert job.status != "ok"

        status = job.status
        if status in ["error", "offline"]:
            self._flush_users_list()
            label = QLabel(_("TEXT_USER_LIST_RETRIEVABLE_FAILURE"))
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            self.layout_users.addWidget(label)
            return
        else:
            errmsg = _("TEXT_USER_LIST_RETRIEVABLE_FAILURE")
        show_error(self, errmsg, exception=job.exc)

    def _on_cancel_invitation_success(self, job):
        assert job.is_finished()
        assert job.status == "ok"

        self.reset()

    def _on_cancel_invitation_error(self, job):
        assert job.is_finished()
        assert job.status != "ok"

        show_error(self, _("TEXT_INVITE_USER_CANCEL_ERROR"), exception=job.exc)

    def _on_invite_user_success(self, job):
        assert job.is_finished()
        assert job.status == "ok"

        self.reset()

    def _on_invite_user_error(self, job):
        assert job.is_finished()
        assert job.status != "ok"

        status = job.status
        show_error(self, handle_invite_errors(status), exception=job.exc)

    def reset(self):
        self.layout_users.clear()

        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "list_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "list_error", QtToTrioJob),
            _do_list_users_and_invitations,
            core=self.core,
        )
