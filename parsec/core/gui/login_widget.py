# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import QWidget

from parsec.core.local_device import list_available_devices
from parsec.core.gui.lang import translate as _
from parsec.core.gui.ui.login_widget import Ui_LoginWidget


class LoginWidget(QWidget, Ui_LoginWidget):
    login_with_password_clicked = pyqtSignal(object, str)

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
        devices = list_available_devices(self.config.config_dir)
        # Display devices in `<organization>:<device_id>` format
        self.devices = {}
        for o, d, t, kf in devices:
            self.combo_username.addItem(f"{o}:{d}")
            self.devices[f"{o}:{d}"] = (o, d, t, kf)
        last_device = self.config.gui_last_device
        if last_device and last_device in self.devices:
            self.combo_username.setCurrentText(last_device)

    def try_login(self):
        if not self.combo_username.currentText():
            return
        *args, key_file = self.devices[self.combo_username.currentText()]
        self.button_login.setDisabled(True)
        self.button_login.setText(_("ACTION_LOGGING_IN"))
        self.login_with_password_clicked.emit(key_file, self.line_edit_password.text())

    def disconnect_all(self):
        pass

    def emit_login_with_password(self, key_file, password):
        self.login_with_password_clicked.emit(key_file, password)
