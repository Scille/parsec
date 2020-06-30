# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QStyle, QStyledItemDelegate, QStyleOption, QStyleOptionViewItem, QWidget

from parsec.core.gui.notification_widget import (
    ErrorNotificationWidget,
    InfoNotificationWidget,
    WarningNotificationWidget,
)
from parsec.core.gui.ui.notification_center_widget import Ui_NotificationCenterWidget


class ItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        view_option = QStyleOptionViewItem(option)
        view_option.decorationAlignment |= Qt.AlignHCenter
        # Even though we told Qt that we don't want any selection on our
        # list, it still adds a blue rectangle on focused items.
        # So we just get rid of focus.
        if option.state & QStyle.State_HasFocus:
            view_option.state &= ~QStyle.State_HasFocus
        super().paint(painter, view_option, index)


class NotificationCenterWidget(QWidget, Ui_NotificationCenterWidget):
    close_requested = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.button_close.clicked.connect(self.close_requested.emit)

    def paintEvent(self, _):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)

    def close_notification(self, notif):
        self.widget_layout.layout().removeWidget(notif)
        notif.hide()
        notif.setParent(None)

    def clear(self):
        while self.widget_layout.layout().count() > 1:
            item = self.widget_layout.layout().takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().setParent(None)

    def add_notification(self, notif_type, msg):
        widget = None
        if notif_type == "ERROR":
            widget = ErrorNotificationWidget()
        elif notif_type == "WARNING":
            widget = WarningNotificationWidget()
        elif notif_type == "INFO":
            widget = InfoNotificationWidget()
        if not widget:
            return
        widget.message = msg
        self.widget_layout.layout().insertWidget(0, widget)
        widget.close_clicked.connect(self.close_notification)
