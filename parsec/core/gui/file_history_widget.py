# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget
from parsec.core.gui.lang import translate as _, format_datetime
from parsec.core.gui.custom_dialogs import show_error, GreyedDialog
from parsec.core.gui.file_size import get_filesize
from parsec.core.gui.ui.file_history_widget import Ui_FileHistoryWidget
from parsec.core.gui.ui.file_history_button import Ui_FileHistoryButton


async def _do_workspace_version(version_lister, path, core):
    versions_list, download_limit_reached = await version_lister.list(
        path, max_manifest_queries=100
    )

    _cache = {}

    async def get_user_info(user_id):
        return _cache.setdefault(user_id, await core.get_user_info(user_id))

    return (
        [(await get_user_info(v.creator.user_id), v) for v in versions_list],
        download_limit_reached,
    )
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
            self.label_src.setText(str(src))
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
        core,
    ):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.dialog = None
        self.core = core
        update_version_list.connect(self.reset_dialog)
        self.get_versions_success.connect(self.on_get_version_success)
        self.get_versions_error.connect(self.on_get_version_error)
        self.button_load_more_entries.clicked.connect(self.load_more)
        self.workspace_fs = workspace_fs
        self.version_lister = workspace_fs.get_version_lister()
        self.set_loading_in_progress(False)
        self.reset_dialog(workspace_fs, self.version_lister, path)

    def set_loading_in_progress(self, in_progress: bool):
        self.loading_in_progress = in_progress
        self.area_list.setVisible(not in_progress)
        self.spinner.setVisible(in_progress)

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
            self.get_versions_success,
            self.get_versions_error,
            _do_workspace_version,
            version_lister=self.version_lister,
            path=self.path,
            core=self.core,
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
        for author, version in versions_list:
            self.add_history_item(
                version=version.version,
                path=self.path,
                creator=author.short_user_display,
                size=version.size,
                timestamp=version.early,
                src_path=version.source,
                dst_path=version.destination,
            )
        self.set_loading_in_progress(False)

    def on_get_version_error(self):
        if self.versions_job and self.versions_job.status != "cancelled":
            show_error(self, _("TEXT_FILE_HISTORY_LIST_FAILURE"), exception=self.versions_job.exc)
        self.versions_job = None
        self.dialog.reject()

    def on_close(self):
        if self.versions_job:
            self.versions_job.cancel_and_join()

    @classmethod
    def show_modal(
        cls,
        jobs_ctx,
        workspace_fs,
        path,
        reload_timestamped_signal,
        update_version_list,
        close_version_list,
        core,
        parent,
        on_finished,
    ):
        w = cls(
            jobs_ctx=jobs_ctx,
            workspace_fs=workspace_fs,
            path=path,
            reload_timestamped_signal=reload_timestamped_signal,
            update_version_list=update_version_list,
            close_version_list=close_version_list,
            core=core,
        )
        d = GreyedDialog(
            w, title=_("TEXT_FILE_HISTORY_TITLE_name").format(name=path.name), parent=parent
        )
        w.dialog = d
        if on_finished:
            d.finished.connect(on_finished)
        # Unlike exec_, show is asynchronous and works within the main Qt loop
        d.show()
        return w
