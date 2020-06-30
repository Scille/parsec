# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QStyle, QStyleOption, QWidget

from parsec.core.gui.ui.menu_widget import Ui_MenuWidget


class MenuWidget(QWidget, Ui_MenuWidget):
    files_clicked = pyqtSignal()
    users_clicked = pyqtSignal()
    devices_clicked = pyqtSignal()
    logout_clicked = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.button_files.clicked.connect(self.files_clicked.emit)
        self.button_users.clicked.connect(self.users_clicked.emit)
        self.button_devices.clicked.connect(self.devices_clicked.emit)
        self.button_logout.clicked.connect(self.logout_clicked.emit)
        self.button_files.apply_style()
        self.button_users.apply_style()
        self.button_devices.apply_style()
        self.button_logout.apply_style()
        self.icon_connection.apply_style()
        self.icon_user.apply_style()

    def paintEvent(self, _):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)

    def activate_files(self):
        self._deactivate_all()
        self.button_files.setChecked(True)

    def activate_devices(self):
        self._deactivate_all()
        self.button_devices.setChecked(True)

    def activate_users(self):
        self._deactivate_all()
        self.button_users.setChecked(True)

    def _deactivate_all(self):
        self.button_files.setChecked(False)
        self.button_users.setChecked(False)
        self.button_devices.setChecked(False)

    def set_connection_state(self, text, tooltip, icon):
        self.label_connection_state.setText(text)
        self.label_connection_state.setToolTip(tooltip)
        self.icon_connection.setPixmap(icon)
        self.icon_connection.apply_style()

    @property
    def organization_url(self):
        return self.label_organization.toolTip()

    @organization_url.setter
    def organization_url(self, val):
        self.label_organization.setToolTip(val)

    @property
    def organization(self):
        return self.label_organization.text()

    @organization.setter
    def organization(self, val):
        self.label_organization.setText(val)

    @property
    def username(self):
        return self.label_username.text()

    @username.setter
    def username(self, val):
        self.label_username.setText(val)

    @property
    def device(self):
        return self.label_device.text()

    @device.setter
    def device(self, val):
        self.label_device.setText(val)
