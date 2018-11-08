from PyQt5.QtCore import pyqtSignal, QCoreApplication, Qt
from PyQt5.QtWidgets import QWidget

from parsec.core.gui.desktop import get_default_device
from parsec.core.gui.custom_widgets import show_error
from parsec.core.gui.core_call import core_call
from parsec.core.gui.ui.login_widget import Ui_LoginWidget
from parsec.core.gui.ui.login_login_widget import Ui_LoginLoginWidget
from parsec.core.gui.ui.login_register_user_widget import Ui_LoginRegisterUserWidget
from parsec.core.gui.ui.login_register_device_widget import Ui_LoginRegisterDeviceWidget


class LoginLoginWidget(QWidget, Ui_LoginLoginWidget):
    login_with_password_clicked = pyqtSignal(str, str)
    login_with_pkcs11_clicked = pyqtSignal(str, str, int, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.button_login.clicked.connect(self.emit_login)
        self.pkcs11_keys = {}
        self.pkcs11_tokens = {}
        self.reset()

    def emit_login(self):
        if self.check_box_use_pkcs11.checkState() == Qt.Unchecked:
            self.login_with_password_clicked.emit(
                self.combo_devices.currentText(), self.line_edit_password.text()
            )
        else:
            self.login_with_pkcs11_clicked.emit(
                self.combo_devices.currentText(),
                self.line_edit_pkcs11_pin.text(),
                self.pkcs11_keys[self.combo_pkcs11_key.currentText()],
                self.pkcs11_tokens[self.combo_pkcs11_token.currentText()],
            )

    def reset(self):
        self.line_edit_password.setText("")
        self.check_box_use_pkcs11.setCheckState(Qt.Unchecked)
        self.line_edit_password.setDisabled(False)
        self.line_edit_pkcs11_pin.setText("")
        if core_call().pkcs11_is_available():
            self.pkcs11_keys = core_call().pkcs11_get_keys()
            self.pkcs11_tokens = core_call().pkcs11_get_tokens()
            self.check_box_use_pkcs11.show()
            self.combo_pkcs11_key.clear()
            for label, index in self.pkcs11_keys.items():
                self.combo_pkcs11_key.addItem(label)
            self.combo_pkcs11_token.clear()
            for label, index in self.pkcs11_tokens.items():
                self.combo_pkcs11_token.addItem(label)
        else:
            self.check_box_use_pkcs11.hide()
        self.widget_pkcs11.hide()

    def add_device(self, device_name):
        self.combo_devices.addItem(device_name)


class LoginRegisterUserWidget(QWidget, Ui_LoginRegisterUserWidget):
    register_with_password_clicked = pyqtSignal(str, str, str, str)
    register_with_pkcs11_clicked = pyqtSignal(str, str, str, int, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.button_register.clicked.connect(self.emit_register)
        self.line_edit_login.textChanged.connect(self.check_infos)
        self.line_edit_device.textChanged.connect(self.check_infos)
        self.line_edit_token.textChanged.connect(self.check_infos)
        self.pkcs11_tokens = {}
        self.pkcs11_keys = {}
        self.reset()

    def check_infos(self, _):
        if (
            len(self.line_edit_login.text())
            and len(self.line_edit_token.text())
            and len(self.line_edit_device.text())
        ):
            self.button_register.setDisabled(False)
        else:
            self.button_register.setDisabled(True)

    def reset(self):
        self.line_edit_login.setText("")
        self.line_edit_password.setText("")
        self.line_edit_password_check.setText("")
        self.line_edit_device.setText(get_default_device())
        self.line_edit_token.setText("")
        self.check_box_use_pkcs11.setCheckState(Qt.Unchecked)
        self.line_edit_password.setDisabled(False)
        self.line_edit_password_check.setDisabled(False)
        if core_call().pkcs11_is_available():
            self.pkcs11_keys = core_call().pkcs11_get_keys()
            self.pkcs11_tokens = core_call().pkcs11_get_tokens()
            self.check_box_use_pkcs11.show()
            self.combo_pkcs11_key.clear()
            for label, index in self.pkcs11_keys.items():
                self.combo_pkcs11_key.addItem(label)
            self.combo_pkcs11_token.clear()
            for label, index in self.pkcs11_tokens.items():
                self.combo_pkcs11_token.addItem(label)
        else:
            self.check_box_use_pkcs11.hide()
        self.widget_pkcs11.hide()

    def emit_register(self):
        if self.check_box_use_pkcs11.checkState() == Qt.Unchecked:
            if (
                len(self.line_edit_password.text()) > 0
                or len(self.line_edit_password_check.text()) > 0
            ):
                if self.line_edit_password.text() != self.line_edit_password_check.text():
                    show_error(
                        self,
                        QCoreApplication.translate(
                            "LoginRegisterUserWidget", "Passwords don't match"
                        ),
                    )
                    return
            self.register_with_password_clicked.emit(
                self.line_edit_login.text(),
                self.line_edit_password.text(),
                self.line_edit_device.text(),
                self.line_edit_token.text(),
            )
        else:
            self.register_with_pkcs11_clicked.emit(
                self.line_edit_login.text(),
                self.line_edit_device.text(),
                self.line_edit_token.text(),
                self.pkcs11_keys[self.combo_pkcs11_key.currentText()],
                self.pkcs11_tokens[self.combo_pkcs11_token.currentText()],
            )


class LoginRegisterDeviceWidget(QWidget, Ui_LoginRegisterDeviceWidget):
    register_with_password_clicked = pyqtSignal(str, str, str, str)
    register_with_pkcs11_clicked = pyqtSignal(str, str, str, str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.button_register.clicked.connect(self.emit_register)
        self.line_edit_login.textChanged.connect(self.check_infos)
        self.line_edit_device.textChanged.connect(self.check_infos)
        self.line_edit_token.textChanged.connect(self.check_infos)
        self.pkcs11_tokens = {}
        self.pkcs11_keys = {}
        self.reset()

    def reset(self):
        self.line_edit_login.setText("")
        self.line_edit_password.setText("")
        self.line_edit_password_check.setText("")
        self.line_edit_device.setText(get_default_device())
        self.line_edit_token.setText("")
        self.line_edit_login.setEnabled(True)
        self.line_edit_password.setEnabled(True)
        self.line_edit_password_check.setEnabled(True)
        self.line_edit_device.setEnabled(True)
        self.line_edit_token.setEnabled(True)
        self.check_box_use_pkcs11.setCheckState(Qt.Unchecked)
        self.line_edit_password.setDisabled(False)
        self.line_edit_password_check.setDisabled(False)
        self.set_error(None)
        if core_call().pkcs11_is_available():
            self.pkcs11_keys = core_call().pkcs11_get_keys()
            self.pkcs11_tokens = core_call().pkcs11_get_tokens()
            self.check_box_use_pkcs11.show()
            self.combo_pkcs11_key.clear()
            for label, index in self.pkcs11_keys.items():
                self.combo_pkcs11_key.addItem(label)
            self.combo_pkcs11_token.clear()
            for label, index in self.pkcs11_tokens.items():
                self.combo_pkcs11_token.addItem(label)
        else:
            self.check_box_use_pkcs11.hide()
        self.widget_pkcs11.hide()

    def check_infos(self, _):
        if (
            len(self.line_edit_login.text())
            and len(self.line_edit_token.text())
            and len(self.line_edit_device.text())
        ):
            self.button_register.setDisabled(False)
        else:
            self.button_register.setDisabled(True)

    def set_error(self, error):
        if not error:
            self.label_error.setText("")
            self.label_error.hide()
        else:
            self.label_error.setText(error)
            self.label_error.show()

    def emit_register(self):
        if self.check_box_use_pkcs11.checkState() == Qt.Unchecked:
            if (
                len(self.line_edit_password.text()) > 0
                or len(self.line_edit_password_check.text()) > 0
            ):
                if self.line_edit_password.text() != self.line_edit_password_check.text():
                    show_error(
                        self,
                        QCoreApplication.translate(
                            "LoginRegisterDeviceWidget", "Passwords don't match."
                        ),
                    )
                    return
            self.register_with_password_clicked.emit(
                self.line_edit_login.text(),
                self.line_edit_password.text(),
                self.line_edit_device.text(),
                self.line_edit_token.text(),
            )
        else:
            self.register_with_pkcs11_clicked.emit(
                self.line_edit_login.text(),
                self.line_edit_device.text(),
                self.line_edit_token.text(),
                self.pkcs11_keys[self.combo_pkcs11_key.currentText()],
                self.pkcs11_tokens[self.combo_pkcs11_token.currentText()],
            )
        self.set_error(
            QCoreApplication.translate(
                "LoginRegisterDeviceWidget", "Waiting for existing device to register us..."
            )
        )
        self.line_edit_login.setEnabled(False)
        self.line_edit_password.setEnabled(False)
        self.line_edit_password_check.setEnabled(False)
        self.line_edit_device.setEnabled(False)
        self.line_edit_token.setEnabled(False)


class LoginWidget(QWidget, Ui_LoginWidget):
    login_with_password_clicked = pyqtSignal(str, str)
    login_with_pkcs11_clicked = pyqtSignal(str, str, int, int)
    register_user_with_password_clicked = pyqtSignal(str, str, str, str)
    register_user_with_pkcs11_clicked = pyqtSignal(str, str, str, int, int)
    register_device_with_password_clicked = pyqtSignal(str, str, str, str)
    register_device_with_pkcs11_clicked = pyqtSignal(str, str, str, int, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)

        self.login_widget = LoginLoginWidget()
        self.layout.insertWidget(0, self.login_widget)
        self.register_user_widget = LoginRegisterUserWidget()
        self.layout.insertWidget(0, self.register_user_widget)
        self.register_device_widget = LoginRegisterDeviceWidget()
        self.layout.insertWidget(0, self.register_device_widget)
        self.button_login_instead.clicked.connect(self.show_login_widget)
        self.button_register_user_instead.clicked.connect(self.show_register_user_widget)
        self.button_register_device_instead.clicked.connect(self.show_register_device_widget)
        self.login_widget.login_with_password_clicked.connect(self.emit_login_with_password)
        self.login_widget.login_with_pkcs11_clicked.connect(self.emit_login_with_pkcs11)
        self.register_user_widget.register_with_password_clicked.connect(
            self.emit_register_user_with_password
        )
        self.register_user_widget.register_with_pkcs11_clicked.connect(
            self.emit_register_user_with_pkcs11
        )
        self.register_device_widget.register_with_password_clicked.connect(
            self.emit_register_device_with_password
        )
        self.register_device_widget.register_with_pkcs11_clicked.connect(
            self.emit_register_device_with_pkcs11
        )
        self.show_login_widget()
        self.reset()

    def emit_register_user_with_password(self, login, password, device, token):
        self.register_user_with_password_clicked.emit(login, password, device, token)

    def emit_register_user_with_pkcs11(self, login, device, token, pkcs11_key, pkcs11_token):
        self.register_user_with_pkcs11_clicked.emit(login, device, token, pkcs11_key, pkcs11_token)

    def emit_register_device_with_password(self, login, password, device, token):
        self.register_device_with_password_clicked.emit(login, password, device, token)

    def emit_register_device_with_pkcs11(self, login, device, token, pkcs11_key, pkcs11_token):
        self.register_device_with_pkcs11_clicked.emit(
            login, device, token, pkcs11_key, pkcs11_token
        )

    def emit_login_with_password(self, login, password):
        self.login_with_password_clicked.emit(login, password)

    def emit_login_with_pkcs11(self, login, pkcs11_pin, pkcs11_key, pkcs11_token):
        self.login_with_pkcs11_clicked.emit(login, pkcs11_pin, pkcs11_key, pkcs11_token)

    def show_login_widget(self):
        self.register_user_widget.hide()
        self.register_device_widget.hide()
        self.login_widget.show()

    def show_register_user_widget(self):
        self.login_widget.hide()
        self.register_device_widget.hide()
        self.register_user_widget.show()

    def show_register_device_widget(self):
        self.login_widget.hide()
        self.register_user_widget.hide()
        self.register_device_widget.show()

    def add_device(self, device_name):
        self.login_widget.add_device(device_name)

    def reset(self):
        self.login_widget.reset()
        self.button_login_instead.hide()
        self.button_register_device_instead.show()
        self.button_register_user_instead.show()
        self.register_user_widget.reset()
        self.register_device_widget.reset()
        self.show_login_widget()
