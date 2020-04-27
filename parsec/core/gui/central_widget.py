# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QPixmap, QColor, QIcon
from PyQt5.QtWidgets import QWidget, QGraphicsDropShadowEffect

from parsec.api.protocol import (
    HandshakeAPIVersionError,
    HandshakeRevokedDevice,
    HandshakeOrganizationExpired,
)
from parsec.core.backend_connection import BackendConnStatus
from parsec.core.fs import (
    FSWorkspaceNoReadAccess,
    FSWorkspaceNoWriteAccess,
    FSWorkspaceInMaintenance,
)

from parsec.core.gui import desktop
from parsec.core.gui.mount_widget import MountWidget
from parsec.core.gui.users_widget import UsersWidget
from parsec.core.gui.devices_widget import DevicesWidget
from parsec.core.gui.menu_widget import MenuWidget
from parsec.core.gui.lang import translate as _
from parsec.core.gui.popup_widget import PopupWidget

from parsec.core.gui.ui.central_widget import Ui_CentralWidget


class CentralWidget(QWidget, Ui_CentralWidget):
    NOTIFICATION_EVENTS = [
        "backend.connection.changed",
        "mountpoint.stopped",
        "mountpoint.remote_error",
        "mountpoint.unhandled_error",
        "sharing.updated",
        "fs.entry.file_conflict_resolved",
    ]

    connection_state_changed = pyqtSignal(object, object)
    logout_requested = pyqtSignal()
    new_notification = pyqtSignal(str, str)

    def __init__(self, core, jobs_ctx, event_bus, **kwargs):
        super().__init__(**kwargs)
        self.setupUi(self)

        self.jobs_ctx = jobs_ctx
        self.core = core
        self.event_bus = event_bus

        self.menu = MenuWidget(parent=self)
        self.widget_menu.layout().addWidget(self.menu)

        for e in self.NOTIFICATION_EVENTS:
            self.event_bus.connect(e, self.handle_event)

        self.label_mountpoint.setText(str(self.core.config.mountpoint_base_dir))
        self.label_mountpoint.clicked.connect(self.open_mountpoint)
        self.menu.organization = self.core.device.organization_addr.organization_id
        self.menu.username = self.core.device.user_id
        self.menu.device = self.core.device.device_name
        self.menu.organization_url = str(self.core.device.organization_addr)

        self.new_notification.connect(self.on_new_notification)
        self.menu.files_clicked.connect(self.show_mount_widget)
        self.menu.users_clicked.connect(self.show_users_widget)
        self.menu.devices_clicked.connect(self.show_devices_widget)
        self.menu.logout_clicked.connect(self.logout_requested.emit)
        self.connection_state_changed.connect(self._on_connection_state_changed)

        self.widget_notif.close_requested.connect(self.button_notifications.toggle)
        self.widget_notif.notification_count_updated.connect(self._on_notification_count_updated)
        self.widget_notif.hide()
        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(0xDD, 0xDD, 0xDD))
        effect.setBlurRadius(10)
        effect.setXOffset(-2)
        effect.setYOffset(2)
        self.widget_notif.setGraphicsEffect(effect)

        self.button_notifications.apply_style()
        self.button_notifications.toggled.connect(self._toggle_notifications_center)
        self.widget_title2.hide()
        self.widget_title3.hide()
        self.title2_icon.apply_style()
        self.title3_icon.apply_style()

        self.icon_mountpoint.apply_style()

        self.mount_widget = MountWidget(self.core, self.jobs_ctx, self.event_bus, parent=self)
        self.widget_central.layout().insertWidget(0, self.mount_widget)
        self.mount_widget.folder_changed.connect(self._on_folder_changed)

        self.users_widget = UsersWidget(self.core, self.jobs_ctx, self.event_bus, parent=self)
        self.widget_central.layout().insertWidget(0, self.users_widget)

        self.devices_widget = DevicesWidget(self.core, self.jobs_ctx, self.event_bus, parent=self)
        self.widget_central.layout().insertWidget(0, self.devices_widget)

        self._on_connection_state_changed(
            self.core.backend_conn.status, self.core.backend_conn.status_exc
        )
        self.popup = PopupWidget(self)

        self.show_mount_widget()

    def _on_notification_count_updated(self, count):
        self.button_notifications.current_color = QColor(0, 0, 0)
        if count == 0:
            self.button_notifications.setIcon(QIcon(":/icons/images/material/notifications.svg"))
        else:
            self.button_notifications.setIcon(
                QIcon(":/icons/images/material/notifications_active.svg")
            )
        self.button_notifications.apply_style()

    def _toggle_notifications_center(self, state):
        if state:
            self.button_notifications.current_color = QColor(0, 0, 0)
            self.button_notifications.setIcon(QIcon(":/icons/images/material/notifications.svg"))
            self.button_notifications.apply_style()
        self.widget_notif.setVisible(state)

    def _on_folder_changed(self, workspace_name, path):
        if workspace_name and path:
            self.widget_title2.show()
            self.label_title2.setText(workspace_name)
            self.widget_title3.show()
            self.label_title3.setText(path)
        else:
            self.widget_title2.hide()
            self.widget_title3.hide()

    def open_mountpoint(self, path):
        desktop.open_file(path)

    def handle_event(self, event, **kwargs):
        if event == "backend.connection.changed":
            self.connection_state_changed.emit(kwargs["status"], kwargs["status_exc"])
        elif event == "mountpoint.stopped":
            self.new_notification.emit(
                "WARNING",
                _("TEXT_NOTIF_WARN_MOUNTPOINT_UNMOUNTED_mountpoint").format(
                    mountpoint=kwargs["mountpoint"]
                ),
            )
        elif event == "mountpoint.remote_error":
            exc = kwargs["exc"]
            path = kwargs["path"]
            if isinstance(exc, FSWorkspaceNoReadAccess):
                msg = _("TEXT_NOTIF_WARN_WORKSPACE_READ_ACCESS_LOST_workspace").format(
                    workspace=path
                )
            elif isinstance(exc, FSWorkspaceNoWriteAccess):
                msg = _("TEXT_NOTIF_WARN_WORKSPACE_WRITE_ACCESS_LOST_workspace").format(
                    workspace=path
                )
            elif isinstance(exc, FSWorkspaceInMaintenance):
                msg = _("TEXT_NOTIF_WARN_WORKSPACE_IN_MAINTENANCE_workspace").format(workspace=path)
            else:
                msg = _("TEXT_NOTIF_WARN_MOUNTPOINT_REMOTE_ERROR_path").format(path=path)
            self.new_notification.emit("WARNING", msg)
        elif event == "mountpoint.unhandled_error":
            exc = kwargs["exc"]
            path = kwargs["path"]
            operation = kwargs["operation"]
            self.new_notification.emit(
                "ERROR",
                _("TEXT_NOTIF_ERR_MOUNTPOINT_UNEXPECTED_ERROR_operation-path").format(
                    operation=operation, path=path
                ),
            )
        elif event == "sharing.updated":
            new_entry = kwargs["new_entry"]
            previous_entry = kwargs["previous_entry"]
            new_role = getattr(new_entry, "role", None)
            previous_role = getattr(previous_entry, "role", None)
            if new_role is not None and previous_role is None:
                self.new_notification.emit(
                    "INFO",
                    _("TEXT_NOTIF_INFO_WORKSPACE_SHARED_workspace").format(
                        workspace=new_entry.name
                    ),
                )
            elif new_role is not None and previous_role is not None:
                self.new_notification.emit(
                    "INFO",
                    _("TEXT_NOTIF_INFO_WORKSPACE_ROLE_UPDATED_workspace").format(
                        workspace=new_entry.name
                    ),
                )
            elif new_role is None and previous_role is not None:
                self.new_notification.emit(
                    "INFO",
                    _("TEXT_NOTIF_INFO_WORKSPACE_UNSHARED_workspace").format(
                        workspace=previous_entry.name
                    ),
                )
        elif event == "fs.entry.file_conflict_resolved":
            self.new_notification.emit(
                "WARNING", _("TEXT_NOTIF_WARN_SYNC_CONFLICT_path").format(path=kwargs["path"])
            )

    def _on_connection_state_changed(self, status, status_exc):
        text = None
        icon = None
        tooltip = None
        notif = None

        if status in (BackendConnStatus.READY, BackendConnStatus.INITIALIZING):
            tooltip = text = _("TEXT_BACKEND_STATE_CONNECTED")
            icon = QPixmap(":/icons/images/material/cloud_queue.svg")

        elif status == BackendConnStatus.LOST:
            tooltip = text = _("TEXT_BACKEND_STATE_DISCONNECTED")
            icon = QPixmap(":/icons/images/material/cloud_off.svg")

        elif status == BackendConnStatus.REFUSED:
            cause = status_exc.__cause__
            if isinstance(cause, HandshakeAPIVersionError):
                tooltip = _("TEXT_BACKEND_STATE_API_MISMATCH_versions").format(
                    versions=", ".join([v.version for v in cause.backend_versions])
                )
            elif isinstance(cause, HandshakeRevokedDevice):
                tooltip = _("TEXT_BACKEND_STATE_REVOKED_DEVICE")
            elif isinstance(cause, HandshakeOrganizationExpired):
                tooltip = _("TEXT_BACKEND_STATE_ORGANIZATION_EXPIRED")
            else:
                tooltip = _("TEXT_BACKEND_STATE_UNKNOWN")
            text = _("TEXT_BACKEND_STATE_DISCONNECTED")
            icon = QPixmap(":/icons/images/material/cloud_off.svg")
            notif = ("WARNING", tooltip)

        elif status == BackendConnStatus.CRASHED:
            text = _("TEXT_BACKEND_STATE_DISCONNECTED")
            tooltip = _("TEXT_BACKEND_STATE_CRASHED_cause").format(cause=str(status_exc.__cause__))
            icon = QPixmap(":/icons/images/material/cloud_off.svg")
            notif = ("ERROR", tooltip)

        self.menu.set_connection_state(text, tooltip, icon)
        if notif:
            self.new_notification.emit(*notif)

    def on_new_notification(self, notif_type, msg):
        self.widget_notif.new_notification(notif_type, msg)
        if self.isVisible() and not self.popup.isVisible():
            self.popup.set_message(msg)
            self.popup.show()

    def show_mount_widget(self):
        self.clear_widgets()
        self.menu.activate_files()
        self.label_title.setText(_("ACTION_MENU_DOCUMENTS"))
        self.mount_widget.show()
        self.mount_widget.show_workspaces_widget()

    def show_users_widget(self):
        self.clear_widgets()
        self.menu.activate_users()
        self.label_title.setText(_("ACTION_MENU_USERS"))
        self.users_widget.show()

    def show_devices_widget(self):
        self.clear_widgets()
        self.menu.activate_devices()
        self.label_title.setText(_("ACTION_MENU_DEVICES"))
        self.devices_widget.show()

    def clear_widgets(self):
        self.widget_title2.hide()
        self.widget_title3.hide()
        self.users_widget.hide()
        self.mount_widget.hide()
        self.devices_widget.hide()
