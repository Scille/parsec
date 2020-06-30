# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget

from parsec.core.gui.lang import translate as _
from parsec.core.gui.parsec_application import ParsecApp
from parsec.core.gui.ui.login_widget import Ui_LoginWidget
from parsec.core.local_device import list_available_devices


class LoginWidget(QWidget, Ui_LoginWidget):
    login_with_password_clicked = pyqtSignal(object, str)
    create_organization_clicked = pyqtSignal()
    join_organization_clicked = pyqtSignal()

    def __init__(self, jobs_ctx, event_bus, config, login_failed_sig, parent):
        super().__init__(parent=parent)
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.event_bus = event_bus
        self.config = config
        self.login_failed_sig = login_failed_sig

        login_failed_sig.connect(self.on_login_failed)
        self.button_login.clicked.connect(self.try_login)
        self.combo_username.currentTextChanged.connect(self.line_edit_password.clear)
        self.button_create_org.clicked.connect(self.create_organization_clicked.emit)
        self.button_join_org.clicked.connect(self.join_organization_clicked.emit)
        self.reload_devices()
        self.button_login.setEnabled(self.combo_username.count() > 0)
        self.line_edit_password.setFocus()

    def on_login_failed(self):
        self.button_login.setEnabled(self.combo_username.count() > 0)
        self.button_login.setText(_("ACTION_LOG_IN"))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return:
            self.try_login()
        event.accept()

    def reload_devices(self):
        while self.combo_username.count():
            self.combo_username.removeItem(0)
        # Display devices in `<organization>:<user>@<device>` format
        self.devices = {}
        for available_device in list_available_devices(self.config.config_dir):
            if not ParsecApp.is_device_connected(
                available_device.organization_id, available_device.device_id
            ):
                name = f"{available_device.organization_id}: {available_device.user_display} @ {available_device.device_display}"
                self.combo_username.addItem(name)
                self.devices[name] = available_device
        last_device = self.config.gui_last_device
        if last_device and last_device in self.devices:
            self.combo_username.setCurrentText(last_device)
        if len(self.devices):
            self.widget_no_device.hide()
            self.widget_login.show()
        else:
            self.widget_no_device.show()
            self.widget_login.hide()
            if ParsecApp.connected_devices:
                self.label_no_device.setText(_("TEXT_LOGIN_NO_AVAILABLE_DEVICE"))
            else:
                self.label_no_device.setText(_("TEXT_LOGIN_NO_DEVICE_ON_MACHINE"))

    def try_login(self):
        if not self.combo_username.currentText():
            return
        available_device = self.devices[self.combo_username.currentText()]
        self.button_login.setDisabled(True)
        self.button_login.setText(_("ACTION_LOGGING_IN"))
        self.login_with_password_clicked.emit(
            available_device.key_file_path, self.line_edit_password.text()
        )

    def disconnect_all(self):
        pass
