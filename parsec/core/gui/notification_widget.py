# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QPixmap, QColor

from parsec.core.gui.ui.notification_widget import Ui_NotificationWidget
from parsec.core.gui.lang import format_datetime


class NotificationWidget(QWidget, Ui_NotificationWidget):
    close_clicked = pyqtSignal(QWidget)

    def __init__(self, color, message):
        super().__init__()
        self.setupUi(self)
        now = pendulum.now()
        self._seen = False
        self.label_icon.setProperty("color", color)
        self.label_icon.apply_style()
        color_name = color.name()
        self.setStyleSheet(
            f"#label_message{{color: {color_name};}} #label_date{{color: {color_name};}}"
        )
        self.message = message
        self.label_date.setText(format_datetime(now))

    @property
    def seen(self):
        return self._seen

    @seen.setter
    def seen(self, v):
        if v:
            self.setStyleSheet(f"#label_message{{color: #999999;}} #label_date{{color: #999999;}}")
            self.label_icon.setProperty("color", QColor(0x99, 0x99, 0x99))
            self.label_icon.setPixmap(QPixmap(":/icons/images/material/error_outline.svg"))
            self.label_icon.apply_style()
        self._seen = v

    @property
    def message(self):
        return self.label_message.text()

    @message.setter
    def message(self, val):
        self.label_message.setText(val)


def create_notification(notif_type, message):
    if notif_type == "ERROR":
        return NotificationWidget(QColor(0xF4, 0x43, 0x36), message)
    elif notif_type == "WARNING":
        return NotificationWidget(QColor(0xF1, 0x96, 0x2B), message)
    elif notif_type == "INFO":
        return NotificationWidget(QColor(0x00, 0x92, 0xFF), message)
