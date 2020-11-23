# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget

from parsec.core.gui.ui.sync_status_widget import Ui_SyncStatusWidget


class SyncStatusWidget(QWidget, Ui_SyncStatusWidget):
    details_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.label.clicked.connect(self.details_clicked.emit)
