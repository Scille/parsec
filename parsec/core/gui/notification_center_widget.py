# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import pyqtSignal, QTimer
from PyQt5.QtWidgets import QWidget

from parsec.core.gui.notification_widget import create_notification
from parsec.core.gui.ui.notification_center_widget import Ui_NotificationCenterWidget


class NotificationCenterWidget(QWidget, Ui_NotificationCenterWidget):
    close_requested = pyqtSignal()
    notification_count_updated = pyqtSignal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setupUi(self)
        self.button_close.clicked.connect(self.close_requested.emit)
        self.button_close.apply_style()
        self.notif_count = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self._set_seen)

    def setVisible(self, toggle):
        super().setVisible(toggle)
        if toggle:
            self.timer.start(4000)

    def _set_seen(self):
        for i in range(self.widget_layout.layout().count() - 1):
            item = self.widget_layout.layout().itemAt(i)
            if item and item.widget():
                item.widget().seen = True

    def new_notification(self, notif_type, msg):
        n = create_notification(notif_type, msg)
        if n:
            self.widget_layout.layout().insertWidget(0, n)
            self.notif_count += 1
            self.notification_count_updated.emit(self.notif_count)
            if self.timer.isActive():
                self.timer.start(4000)

    def clear(self):
        while self.widget_layout.layout().count() > 1:
            item = self.widget_layout.layout().takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().setParent(None)
