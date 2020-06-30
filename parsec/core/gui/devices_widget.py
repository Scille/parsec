# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QGraphicsDropShadowEffect, QMenu, QWidget

from parsec.core.backend_connection import BackendNotAvailable
from parsec.core.gui.flow_layout import FlowLayout
from parsec.core.gui.invite_device_widget import InviteDeviceWidget
from parsec.core.gui.lang import translate as _
from parsec.core.gui.password_change_widget import PasswordChangeWidget
from parsec.core.gui.trio_thread import JobResultError, QtToTrioJob, ThreadSafeQtSignal
from parsec.core.gui.ui.device_button import Ui_DeviceButton
from parsec.core.gui.ui.devices_widget import Ui_DevicesWidget


class DeviceButton(QWidget, Ui_DeviceButton):
    change_password_clicked = pyqtSignal(str)

    def __init__(self, device_name, is_current_device, certified_on):
        super().__init__()
        self.setupUi(self)
        self.is_current_device = is_current_device
        self.label_icon.apply_style()
        self.device_name = device_name
        self.certified_on = certified_on
        self.label_device_name.setText(self.device_name)
        if self.is_current_device:
            self.label_is_current.setText("({})".format(_("TEXT_DEVICE_IS_CURRENT")))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(0x99, 0x99, 0x99))
        effect.setBlurRadius(10)
        effect.setXOffset(2)
        effect.setYOffset(2)
        self.setGraphicsEffect(effect)

    def show_context_menu(self, pos):
        if not self.is_current_device:
            return
        global_pos = self.mapToGlobal(pos)
        menu = QMenu(self)
        action = menu.addAction(_("ACTION_DEVICE_MENU_CHANGE_PASSWORD"))
        action.triggered.connect(self.change_password)
        menu.exec_(global_pos)

    def change_password(self):
        self.change_password_clicked.emit(self.device_name)


async def _do_list_devices(core):
    try:
        return await core.get_user_devices_info()
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    # TODO : handle all errors from the remote_devices_manager and notify GUI
    # Raises:
    #     RemoteDevicesManagerError
    #     RemoteDevicesManagerBackendOfflineError
    #     RemoteDevicesManagerNotFoundError
    #     RemoteDevicesManagerInvalidTrustchainError


class DevicesWidget(QWidget, Ui_DevicesWidget):
    list_success = pyqtSignal(QtToTrioJob)
    list_error = pyqtSignal(QtToTrioJob)

    def __init__(self, core, jobs_ctx, event_bus, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.core = core
        self.event_bus = event_bus
        self.layout_devices = FlowLayout(spacing=40)
        self.layout_content.addLayout(self.layout_devices)
        self.devices = []
        self.button_add_device.clicked.connect(self.invite_new_device)
        self.button_add_device.apply_style()
        self.filter_timer = QTimer()
        self.filter_timer.setInterval(300)
        self.list_success.connect(self.on_list_success)
        self.list_error.connect(self.on_list_error)
        self.line_edit_search.textChanged.connect(self.filter_timer.start)
        self.filter_timer.timeout.connect(self.on_filter_timer_timeout)
        self.reset()

    def on_filter_timer_timeout(self):
        self.filter_devices(self.line_edit_search.text())

    def filter_devices(self, pattern):
        pattern = pattern.lower()
        for i in range(self.layout_devices.count()):
            item = self.layout_devices.itemAt(i)
            if item:
                w = item.widget()
                if pattern and pattern not in w.device_name.lower():
                    w.hide()
                else:
                    w.show()

    def change_password(self, device_name):
        PasswordChangeWidget.exec_modal(core=self.core, parent=self)

    def invite_new_device(self):
        InviteDeviceWidget.exec_modal(core=self.core, jobs_ctx=self.jobs_ctx, parent=self)
        self.reset()

    def add_device(self, device_name, is_current_device, certified_on):
        if device_name in self.devices:
            return
        button = DeviceButton(device_name, is_current_device, certified_on)
        self.layout_devices.addWidget(button)
        button.change_password_clicked.connect(self.change_password)
        button.show()
        self.devices.append(device_name)

    def on_list_success(self, job):
        devices = job.ret
        current_device = self.core.device
        self.devices = []
        self.layout_devices.clear()
        for device in devices:
            self.add_device(
                device.device_name,
                is_current_device=device.device_name == current_device.device_name,
                certified_on=device.created_on,
            )

    def on_list_error(self, job):
        pass

    def reset(self):
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "list_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "list_error", QtToTrioJob),
            _do_list_devices,
            core=self.core,
        )
