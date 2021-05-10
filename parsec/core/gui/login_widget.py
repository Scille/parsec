# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from pathlib import Path

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import QWidget

from parsec.core.local_device import list_available_devices, AvailableDevice

from parsec.core.gui.lang import translate as _
from parsec.core.gui.parsec_application import ParsecApp
from parsec.core.gui import desktop
from parsec.core.gui.ui.login_widget import Ui_LoginWidget
from parsec.core.gui.ui.account_button import Ui_AccountButton
from parsec.core.gui.ui.login_accounts_widget import Ui_LoginAccountsWidget
from parsec.core.gui.ui.login_password_input_widget import Ui_LoginPasswordInputWidget
from parsec.core.gui.ui.login_no_devices_widget import Ui_LoginNoDevicesWidget


class AccountButton(QWidget, Ui_AccountButton):
    clicked = pyqtSignal(AvailableDevice)

    def __init__(self, device):
        super().__init__()
        self.setupUi(self)
        self.device = device
        self.label_device.setText(self.device.device_display)
        self.label_name.setText(self.device.short_user_display)
        self.label_organization.setText(self.device.organization_id)

    def mousePressEvent(self, event):
        if event.button() & Qt.LeftButton:
            self.clicked.emit(self.device)


class LoginAccountsWidget(QWidget, Ui_LoginAccountsWidget):
    account_clicked = pyqtSignal(AvailableDevice)

    def __init__(self, devices):
        super().__init__()
        self.setupUi(self)
        for available_device in devices:
            ab = AccountButton(available_device)
            ab.clicked.connect(self.account_clicked.emit)
            self.accounts_widget.layout().insertWidget(0, ab)

    def reset(self):
        pass


class LoginPasswordInputWidget(QWidget, Ui_LoginPasswordInputWidget):
    back_clicked = pyqtSignal()
    log_in_clicked = pyqtSignal(Path, str)

    def __init__(self, device, hide_back=False):
        super().__init__()
        self.setupUi(self)
        self.device = device
        if hide_back:
            self.button_back.hide()
        self.button_back.clicked.connect(self.back_clicked.emit)
        self.button_login.clicked.connect(self._on_log_in_clicked)
        self.label_instructions.setText(
            _("TEXT_LOGIN_ENTER_PASSWORD_INSTRUCTIONS_organization-device-user-email").format(
                organization=self.device.organization_id,
                user=self.device.short_user_display,
                device=self.device.device_display,
                email="",
            )
        )
        self.line_edit_password.textChanged.connect(self._on_password_text_changed)
        self.label_caps_lock.setVisible(desktop.is_caps_lock_on())

    def _on_log_in_clicked(self):
        self.button_login.setDisabled(True)
        self.button_login.setText(_("ACTION_LOGGING_IN"))
        self.log_in_clicked.emit(self.device.key_file_path, self.line_edit_password.text())

    def _on_password_text_changed(self, _):
        self.label_caps_lock.setVisible(desktop.is_caps_lock_on())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.button_login.isEnabled():
            self._on_log_in_clicked()
        event.accept()

    def reset(self):
        self.button_login.setDisabled(False)
        self.line_edit_password.setText("")
        self.button_login.setText(_("ACTION_LOG_IN"))


class LoginNoDevicesWidget(QWidget, Ui_LoginNoDevicesWidget):
    join_organization_clicked = pyqtSignal()
    create_organization_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setupUi(self)
        if ParsecApp.connected_devices:
            self.label_no_device.setText(_("TEXT_LOGIN_NO_AVAILABLE_DEVICE"))
        else:
            self.label_no_device.setText(_("TEXT_LOGIN_NO_DEVICE_ON_MACHINE"))
        self.button_create_org.clicked.connect(self.create_organization_clicked.emit)
        self.button_join_org.clicked.connect(self.join_organization_clicked.emit)

    def reset(self):
        pass


class LoginWidget(QWidget, Ui_LoginWidget):
    login_with_password_clicked = pyqtSignal(Path, str)
    create_organization_clicked = pyqtSignal()
    join_organization_clicked = pyqtSignal()
    login_canceled = pyqtSignal()

    def __init__(self, jobs_ctx, event_bus, config, login_failed_sig, parent):
        super().__init__(parent=parent)
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.event_bus = event_bus
        self.config = config
        self.login_failed_sig = login_failed_sig

        login_failed_sig.connect(self.on_login_failed)
        self.reload_devices()

    def on_login_failed(self):
        item = self.widget.layout().itemAt(0)
        if item:
            lw = item.widget()
            lw.reset()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.button_login.isEnabled():
            self.try_login()
        event.accept()

    def reload_devices(self):
        self._clear_widget()
        devices = [
            device
            for device in list_available_devices(self.config.config_dir)
            if not ParsecApp.is_device_connected(device.organization_id, device.device_id)
        ]
        if not len(devices):
            no_device_widget = LoginNoDevicesWidget()
            no_device_widget.create_organization_clicked.connect(
                self.create_organization_clicked.emit
            )
            no_device_widget.join_organization_clicked.connect(self.join_organization_clicked.emit)
            self.widget.layout().addWidget(no_device_widget)
            no_device_widget.setFocus()
        elif len(devices) == 1:
            self._on_account_clicked(devices[0], hide_back=True)
        else:
            accounts_widget = LoginAccountsWidget(devices)
            accounts_widget.account_clicked.connect(self._on_account_clicked)
            self.widget.layout().addWidget(accounts_widget)
            accounts_widget.setFocus()

    def _clear_widget(self):
        while self.widget.layout().count() != 0:
            item = self.widget.layout().takeAt(0)
            if item:
                w = item.widget()
                self.widget.layout().removeWidget(w)
                w.hide()
                w.setParent(None)

    def _on_account_clicked(self, device, hide_back=False):
        self._clear_widget()
        lw = LoginPasswordInputWidget(device, hide_back=hide_back)
        lw.back_clicked.connect(self._on_back_clicked)
        lw.log_in_clicked.connect(self.try_login)
        self.widget.layout().addWidget(lw)
        lw.line_edit_password.setFocus()

    def _on_back_clicked(self):
        self.login_canceled.emit()
        self.reload_devices()

    def try_login(self, key_file, password):
        self.login_with_password_clicked.emit(key_file, password)

    def disconnect_all(self):
        pass
