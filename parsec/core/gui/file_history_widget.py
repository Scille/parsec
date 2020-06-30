# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtSvg import QSvgWidget
from PyQt5.QtWidgets import QWidget

from parsec.core.gui.custom_dialogs import GreyedDialog, show_error
from parsec.core.gui.file_size import get_filesize
from parsec.core.gui.lang import format_datetime
from parsec.core.gui.lang import translate as _
from parsec.core.gui.trio_thread import ThreadSafeQtSignal
from parsec.core.gui.ui.file_history_button import Ui_FileHistoryButton
from parsec.core.gui.ui.file_history_widget import Ui_FileHistoryWidget


async def _do_workspace_version(version_lister, path):
    return await version_lister.list(path, max_manifest_queries=100)
    # TODO : check no exception raised, create tests...


class FileHistoryButton(QWidget, Ui_FileHistoryButton):
    def __init__(self, version, creator, name, size, src, dst, timestamp):
        super().__init__()
        self.setupUi(self)
        self.label_version.setText(str(version))
        self.label_user.setText(creator)
        self.label_size.setText(get_filesize(size) if size is not None else "")
        self.label_date.setText(format_datetime(timestamp))
        if not src:
            self.label_src.hide()
        else:
            self.label_src.setText(src)
        if not dst:
            self.label_dst.hide()
        else:
            self.label_dst.setText(str(dst))


class FileHistoryWidget(QWidget, Ui_FileHistoryWidget):
    get_versions_success = pyqtSignal()
    get_versions_error = pyqtSignal()

    def __init__(
        self,
        jobs_ctx,
        workspace_fs,
        path,
        reload_timestamped_signal,
        update_version_list,
        close_version_list,
    ):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        update_version_list.connect(self.reset_dialog)
        self.get_versions_success.connect(self.on_get_version_success)
        self.get_versions_error.connect(self.on_get_version_error)
        self.button_load_more_entries.clicked.connect(self.load_more)
        self.workspace_fs = workspace_fs
        self.version_lister = workspace_fs.get_version_lister()
        self.spinner = QSvgWidget(":/icons/images/icons/spinner.svg")
        self.spinner.setFixedSize(100, 100)
        self.spinner_layout.addWidget(self.spinner, Qt.AlignCenter)
        self.spinner_layout.setAlignment(Qt.AlignCenter)
        self.set_loading_in_progress(False)
        self.reset_dialog(workspace_fs, self.version_lister, path)

    def set_loading_in_progress(self, in_progress: bool):
        self.loading_in_progress = in_progress
        self.area_list.setVisible(not in_progress)
        self.spinner_frame.setVisible(in_progress)

    def reset_dialog(self, workspace_fs, version_lister, path):
        if self.loading_in_progress:
            return
        self.set_loading_in_progress(True)
        self.workspace_fs = workspace_fs
        self.path = path
        self.reset_list()

    def load_more(self):
        if self.loading_in_progress:
            return
        self.set_loading_in_progress(True)
        self.reset_list()

    def reset_list(self):
        while self.layout_history.count() != 0:
            item = self.layout_history.takeAt(0)
            if item:
                w = item.widget()
                self.layout_history.removeWidget(w)
                w.hide()
                w.setParent(0)
        self.versions_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "get_versions_success"),
            ThreadSafeQtSignal(self, "get_versions_error"),
            _do_workspace_version,
            version_lister=self.version_lister,
            path=self.path,
        )

    def add_history_item(self, version, path, creator, size, timestamp, src_path, dst_path):
        button = FileHistoryButton(
            version=version,
            creator=creator,
            name=path,
            size=size,
            src=src_path,
            dst=dst_path,
            timestamp=timestamp,
        )
        self.layout_history.addWidget(button)
        button.show()

    def on_get_version_success(self):
        versions_list, download_limit_reached = self.versions_job.ret
        if download_limit_reached:
            self.button_load_more_entries.setVisible(False)
        self.versions_job = None
        for v in versions_list:
            self.add_history_item(
                version=v.version,
                path=self.path,
                creator=v.creator,
                size=v.size,
                timestamp=v.early,
                src_path=v.source,
                dst_path=v.destination,
            )
        self.set_loading_in_progress(False)

    def on_get_version_error(self):
        if self.versions_job and self.versions_job.status != "cancelled":
            show_error(self, _("TEXT_FILE_HISTORY_LIST_FAILURE"), exception=self.versions_job.exc)
        self.versions_job = None
        self.reject()

    def on_close(self):
        if self.versions_job:
            self.versions_job.cancel_and_join()

    @classmethod
    def exec_modal(
        cls,
        jobs_ctx,
        workspace_fs,
        path,
        reload_timestamped_signal,
        update_version_list,
        close_version_list,
        parent,
    ):
        w = cls(
            jobs_ctx=jobs_ctx,
            workspace_fs=workspace_fs,
            path=path,
            reload_timestamped_signal=reload_timestamped_signal,
            update_version_list=update_version_list,
            close_version_list=close_version_list,
        )
        d = GreyedDialog(
            w, title=_("TEXT_FILE_HISTORY_TITLE_name").format(name=path.name), parent=parent
        )
        return d.exec_()
