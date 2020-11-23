# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import random

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import QWidget, QListWidgetItem

from parsec.core.gui.ui.file_sync_widget import Ui_FileSyncWidget
from parsec.core.gui.ui.sync_widget import Ui_SyncWidget


class FileSyncWidget(QWidget, Ui_FileSyncWidget):
    def __init__(self, file_name, workspace_name):
        super().__init__()
        self.setupUi(self)
        self.progress.setValue(random.randint(0, 100))
        self.progress.setFormat(f"{workspace_name} - {file_name} - %p%")


class SyncWidget(QWidget, Ui_SyncWidget):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        for _ in range(10):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 60))
            self.upload_list.addItem(item)
            self.upload_list.setItemWidget(
                item, FileSyncWidget("MonFichierQUiEstUploadé.txt", "MonWorkspace")
            )
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 60))
            self.download_list.addItem(item)
            self.download_list.setItemWidget(
                item, FileSyncWidget("MonFichierQuiEstDownloadé.txt", "MonWorkspace")
            )
