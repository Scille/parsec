# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS
import trio
import pathlib

from parsec.core.core_events import CoreEvent
from uuid import UUID
from parsec.datetime import DateTime
from enum import IntEnum
from structlog import get_logger

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import QFileDialog, QWidget
from parsec.core.types import FsPath, WorkspaceEntry, WorkspaceRole, BackendOrganizationFileLinkAddr
from parsec.core.fs import WorkspaceFS, WorkspaceFSTimestamped
from parsec.core.fs.exceptions import (
    FSRemoteManifestNotFound,
    FSInvalidArgumentError,
    FSFileNotFoundError,
)

from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal, QtToTrioJob
from parsec.core.gui import desktop
from parsec.core.gui.file_items import FileType, TYPE_DATA_INDEX, UUID_DATA_INDEX
from parsec.core.gui.custom_dialogs import (
    ask_question,
    show_error,
    get_text_input,
    show_info,
    GreyedDialog,
)

from parsec.core.gui.custom_widgets import CenteredSpinnerWidget
from parsec.core.gui.file_history_widget import FileHistoryWidget
from parsec.core.gui.loading_widget import LoadingWidget
from parsec.core.gui.lang import translate as _
from parsec.core.gui.workspace_roles import get_role_translation
from parsec.core.gui.ui.files_widget import Ui_FilesWidget
from parsec.core.gui.file_table import PasteStatus
from parsec.core.types import DEFAULT_BLOCK_SIZE


logger = get_logger()


class CancelException(Exception):
    pass


async def _do_rename(workspace_fs, paths):
    new_names = {}
    for (old_path, new_path, uuid) in paths:
        try:
            await workspace_fs.rename(old_path, new_path)
            new_names[uuid] = FsPath(new_path).name
        except FileExistsError as exc:
            raise JobResultError("already-exists", multi=len(paths) > 1) from exc
        except OSError as exc:
            raise JobResultError("not-empty", multi=len(paths) > 1) from exc


async def _do_delete(workspace_fs, files, silent=False):
    for path, file_type in files:
        try:
            if file_type == FileType.Folder:
                await workspace_fs.rmtree(path)
            else:
                await workspace_fs.unlink(path)
        except Exception as exc:
            if not silent:
                raise JobResultError("error", multi=len(files) > 1) from exc


async def _do_copy_files(workspace_fs, current_directory, files, source_workspace):
    last_exc = None
    error_count = 0
    for src, src_type in files:
        # In order to be able to rename the file if a file of the same name already exists
        # we need the name without extensions.
        name_we, *_ = src.name.split(".", 1)
        count = 2
        file_name = src.name
        while True:
            try:
                dst = current_directory / file_name
                if src_type == FileType.Folder:
                    await workspace_fs.copytree(src, dst, source_workspace)
                else:
                    await workspace_fs.copyfile(src, dst, source_workspace)
                break
            except FileExistsError:
                # File already exists, we append a counter at the end of its name
                file_name = "{} ({}){}".format(
                    name_we, count, "".join(pathlib.Path(src.name).suffixes)
                )
                count += 1
            except FSInvalidArgumentError as exc:
                # Move a file onto itself
                # Not a big deal for files, we just do nothing and pretend we
                # actually did something
                # For folders we have to warn the user
                if src_type == FileType.Folder:
                    error_count += 1
                    last_exc = exc
                break
            except Exception as exc:
                # No idea what happened, we'll just warn the user that we encountered an
                # unexcepted error and log it
                error_count += 1
                last_exc = exc
                logger.exception("Unhandled error while cut/copy file", exc_info=exc)
                break
    if error_count:
        raise JobResultError("error", last_exc=last_exc, error_count=error_count)


async def _do_move_files(workspace_fs, current_directory, files, source_workspace):
    error_count = 0
    last_exc = None
    for src, src_type in files:
        # In order to be able to rename the file if a file of the same name already exists
        # we need the name without extensions.
        name_we, *_ = src.name.split(".", 1)
        file_name = src.name
        count = 2
        while True:
            try:
                dst = current_directory / file_name
                await workspace_fs.move(src, dst, source_workspace)
                break
            except FileExistsError:
                # File already exists, we append a counter at the end of its name
                file_name = "{} ({}){}".format(
                    name_we, count, "".join(pathlib.Path(src.name).suffixes)
                )
                count += 1
            except FSInvalidArgumentError as exc:
                # Move a file onto itself
                # Not a big deal for files, we just do nothing and pretend we
                # actually did something
                # For folders we have to warn the user
                if src_type == FileType.Folder:
                    error_count += 1
                    last_exc = exc
                break
            except Exception as exc:
                # No idea what happened, we'll just warn the user that we encountered an
                # unexcepted error and log it
                error_count += 1
                last_exc = exc
                logger.exception("Unhandled error while cut/copy file", exc_info=exc)
                break
    if error_count:
        raise JobResultError("error", last_exc=last_exc, error_count=error_count)


async def _do_folder_stat(workspace_fs, path, default_selection):
    stats = {}
    dir_stat = await workspace_fs.path_info(path)
    for child in dir_stat["children"]:
        try:
            child_stat = await workspace_fs.path_info(path / child)
        except FSRemoteManifestNotFound as exc:
            child_stat = {"type": "inconsistency", "id": exc.args[0]}
        stats[child] = child_stat
    return path, dir_stat["id"], stats, default_selection


async def _do_folder_create(workspace_fs, path):
    try:
        await workspace_fs.mkdir(path)
    except FileExistsError as exc:
        raise JobResultError("already-exists") from exc


async def _do_import(workspace_fs, files, total_size, progress_signal):
    current_size = 0
    errors = []
    for src, dst in files:
        try:
            if dst.parent != FsPath("/"):
                await workspace_fs.mkdir(dst.parent, parents=True, exist_ok=True)
            progress_signal.emit(src.name, current_size)

            async with await trio.open_file(src, "rb") as f:
                async with await workspace_fs.open_file(dst, "wb") as dest_file:
                    read_size = 0
                    while True:
                        chunk = await f.read(DEFAULT_BLOCK_SIZE)
                        if not chunk:
                            break
                        await dest_file.write(chunk)
                        read_size += len(chunk)
                        progress_signal.emit(src.name, current_size + read_size)
            current_size += read_size + 1
            progress_signal.emit(src.name, current_size)
        except trio.Cancelled as exc:
            errors.append(exc)
            raise JobResultError(
                "cancelled", last_file=dst, file_count=len(files), exceptions=errors
            ) from exc
        except PermissionError as exc:
            errors.append(exc)
    if errors:
        raise JobResultError("error", exceptions=errors, file_count=len(files))


async def _do_remount_timestamped(
    mountpoint_manager,
    workspace_fs,
    timestamp,
    path,
    file_type,
    open_after_load,
    close_after_load,
    reload_after_remount,
):
    await mountpoint_manager.remount_workspace_new_timestamp(
        workspace_fs.workspace_id,
        workspace_fs.timestamp if isinstance(workspace_fs, WorkspaceFSTimestamped) else None,
        timestamp,
    )
    # TODO : get it directly from mountpoint_manager if API evolves
    workspace_fs = await workspace_fs.to_timestamped(timestamp)
    await workspace_fs.path_info(path)  # Checks path is valid when remounted
    return (workspace_fs, path, file_type, open_after_load, close_after_load, reload_after_remount)


class Clipboard:
    class Status(IntEnum):
        Copied = 1
        Cut = 2

    def __init__(self, files, status, source_workspace=None):
        self.files = files
        self.source_workspace = source_workspace
        self.status = status


class FilesWidget(QWidget, Ui_FilesWidget):
    fs_updated_qt = pyqtSignal(CoreEvent, UUID)
    fs_synced_qt = pyqtSignal(CoreEvent, UUID)
    entry_downsynced_qt = pyqtSignal(UUID, UUID)
    global_clipboard_updated_qt = pyqtSignal(object)

    sharing_updated_qt = pyqtSignal(WorkspaceEntry, object)
    back_clicked = pyqtSignal()

    rename_success = pyqtSignal(QtToTrioJob)
    rename_error = pyqtSignal(QtToTrioJob)
    delete_success = pyqtSignal(QtToTrioJob)
    delete_error = pyqtSignal(QtToTrioJob)
    folder_stat_success = pyqtSignal(QtToTrioJob)
    folder_stat_error = pyqtSignal(QtToTrioJob)
    folder_create_success = pyqtSignal(QtToTrioJob)
    folder_create_error = pyqtSignal(QtToTrioJob)
    import_success = pyqtSignal(QtToTrioJob)
    import_error = pyqtSignal(QtToTrioJob)

    copy_success = pyqtSignal(QtToTrioJob)
    copy_error = pyqtSignal(QtToTrioJob)
    move_success = pyqtSignal(QtToTrioJob)
    move_error = pyqtSignal(QtToTrioJob)

    import_progress = pyqtSignal(str, int)

    reload_timestamped_requested = pyqtSignal(DateTime, FsPath, FileType, bool, bool, bool)
    reload_timestamped_success = pyqtSignal(QtToTrioJob)
    reload_timestamped_error = pyqtSignal(QtToTrioJob)
    update_version_list = pyqtSignal(WorkspaceFS, FsPath)
    close_version_list = pyqtSignal()

    folder_changed = pyqtSignal(str, str)

    def __init__(self, core, jobs_ctx, event_bus, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)

        # Create spinner and stack it on the table_files widget
        self.spinner = CenteredSpinnerWidget(parent=self.table_files)
        self.table_files_layout.addWidget(self.spinner, 0, 0)
        self.spinner.hide()

        self.core = core
        self.jobs_ctx = jobs_ctx
        self.event_bus = event_bus
        self.workspace_fs = None
        self.import_job = None
        self.clipboard = None

        self.button_back.clicked.connect(self.back_clicked)
        self.button_back.apply_style()
        self.button_import_folder.clicked.connect(self.import_folder_clicked)
        self.button_import_folder.apply_style()
        self.button_import_files.clicked.connect(self.import_files_clicked)
        self.button_import_files.apply_style()
        self.button_create_folder.clicked.connect(self.create_folder_clicked)
        self.button_create_folder.apply_style()
        self.line_edit_search.textChanged.connect(self.filter_files)
        self.line_edit_search.hide()
        self.current_directory = FsPath("/")
        self.current_directory_uuid = None
        self.fs_updated_qt.connect(self._on_fs_updated_qt)
        self.fs_synced_qt.connect(self._on_fs_synced_qt)
        self.entry_downsynced_qt.connect(self._on_entry_downsynced_qt)
        self.update_timer = QTimer()
        self.update_timer.setInterval(1000)
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self.reload)
        self.default_import_path = str(pathlib.Path.home())
        self.table_files.config = self.core.config
        self.table_files.file_moved.connect(self.on_file_moved)
        self.table_files.item_activated.connect(self.item_activated)
        self.table_files.rename_clicked.connect(self.rename_files)
        self.table_files.delete_clicked.connect(self.delete_files)
        self.table_files.open_clicked.connect(self.open_files)
        self.table_files.files_dropped.connect(self.on_files_dropped)
        self.table_files.show_history_clicked.connect(self.show_history)
        self.table_files.paste_clicked.connect(self.on_paste_clicked)
        self.table_files.copy_clicked.connect(self.on_copy_clicked)
        self.table_files.cut_clicked.connect(self.on_cut_clicked)
        self.table_files.file_path_clicked.connect(self.on_get_file_path_clicked)
        self.table_files.open_current_dir_clicked.connect(self.on_open_current_dir_clicked)

        self.sharing_updated_qt.connect(self._on_sharing_updated_qt)
        self.rename_success.connect(self._on_rename_success)
        self.rename_error.connect(self._on_rename_error)
        self.delete_success.connect(self._on_delete_success)
        self.delete_error.connect(self._on_delete_error)
        self.folder_stat_success.connect(self._on_folder_stat_success)
        self.folder_stat_error.connect(self._on_folder_stat_error)
        self.folder_create_success.connect(self._on_folder_create_success)
        self.folder_create_error.connect(self._on_folder_create_error)
        self.import_success.connect(self._on_import_success)
        self.import_error.connect(self._on_import_error)
        self.copy_success.connect(self._on_copy_success)
        self.copy_error.connect(self._on_copy_error)
        self.move_success.connect(self._on_move_success)
        self.move_error.connect(self._on_move_error)

        self.reload_timestamped_requested.connect(self._on_reload_timestamped_requested)
        self.reload_timestamped_success.connect(self._on_reload_timestamped_success)
        self.reload_timestamped_error.connect(self._on_reload_timestamped_error)

        self.loading_dialog = None
        self.import_progress.connect(self._on_import_progress)

        self.event_bus.connect(CoreEvent.FS_ENTRY_UPDATED, self._on_fs_entry_updated_trio)
        self.event_bus.connect(CoreEvent.FS_ENTRY_SYNCED, self._on_fs_entry_synced_trio)
        self.event_bus.connect(CoreEvent.SHARING_UPDATED, self._on_sharing_updated_trio)
        self.event_bus.connect(CoreEvent.FS_ENTRY_DOWNSYNCED, self._on_entry_downsynced_trio)

    def disconnect_all(self):
        pass

    def set_workspace_fs(
        self, wk_fs, current_directory=FsPath("/"), default_selection=None, clipboard=None
    ):
        self.current_directory = current_directory
        self.workspace_fs = wk_fs
        ws_entry = self.jobs_ctx.run_sync(self.workspace_fs.get_workspace_entry)
        self.current_user_role = ws_entry.role
        self.label_role.setText(get_role_translation(self.current_user_role))
        self.table_files.current_user_role = self.current_user_role
        if self.current_user_role == WorkspaceRole.READER:
            self.button_import_folder.hide()
            self.button_import_files.hide()
            self.button_create_folder.hide()
        else:
            self.button_import_folder.show()
            self.button_import_files.show()
            self.button_create_folder.show()
        self.clipboard = clipboard
        if not self.clipboard:
            self.table_files.paste_status = PasteStatus(status=PasteStatus.Status.Disabled)
        else:
            if self.clipboard.source_workspace == self.workspace_fs:
                self.table_files.paste_status = PasteStatus(status=PasteStatus.Status.Enabled)
            else:
                # Sending the source_workspace name for paste text
                self.table_files.paste_status = PasteStatus(
                    status=PasteStatus.Status.Enabled,
                    source_workspace=str(
                        self.jobs_ctx.run_sync(self.clipboard.source_workspace.get_workspace_name)
                    ),
                )
        self.reset(default_selection)

    def reset(self, default_selection=None):
        workspace_name = self.jobs_ctx.run_sync(self.workspace_fs.get_workspace_name)
        self.load(self.current_directory, default_selection)
        self.table_files.sortItems(0)
        self.folder_changed.emit(str(workspace_name), str(self.current_directory))

    def on_get_file_path_clicked(self):
        files = self.table_files.selected_files()
        if len(files) != 1:
            return
        url = BackendOrganizationFileLinkAddr.build(
            self.core.device.organization_addr,
            self.workspace_fs.workspace_id,
            self.current_directory / files[0].name,
        )
        desktop.copy_to_clipboard(str(url))
        show_info(self, _("TEXT_FILE_LINK_COPIED_TO_CLIPBOARD"))

    def on_copy_clicked(self):
        files = self.table_files.selected_files()
        files_to_copy = []
        for f in files:
            if f.type != FileType.Folder and f.type != FileType.File:
                continue
            files_to_copy.append((self.current_directory / f.name, f.type))
        self.clipboard = Clipboard(
            files=files_to_copy, status=Clipboard.Status.Copied, source_workspace=self.workspace_fs
        )
        self.global_clipboard_updated_qt.emit(self.clipboard)
        self.table_files.paste_status = PasteStatus(status=PasteStatus.Status.Enabled)

    def on_cut_clicked(self):
        files = self.table_files.selected_files()
        files_to_cut = []
        rows = []

        for f in files:
            if f.type != FileType.Folder and f.type != FileType.File:
                continue
            rows.append(f.row)
            files_to_cut.append((self.current_directory / f.name, f.type))
        self.table_files.set_rows_cut(rows)
        self.clipboard = Clipboard(
            files=files_to_cut, status=Clipboard.Status.Cut, source_workspace=self.workspace_fs
        )
        self.global_clipboard_updated_qt.emit(self.clipboard)
        self.table_files.paste_status = PasteStatus(status=PasteStatus.Status.Enabled)

    def on_paste_clicked(self):
        if not self.clipboard:
            return

        if self.clipboard.status == Clipboard.Status.Cut:
            self.jobs_ctx.submit_job(
                ThreadSafeQtSignal(self, "move_success", QtToTrioJob),
                ThreadSafeQtSignal(self, "move_error", QtToTrioJob),
                _do_move_files,
                workspace_fs=self.workspace_fs,
                current_directory=self.current_directory,
                files=self.clipboard.files,
                source_workspace=self.clipboard.source_workspace,
            )
            self.clipboard = None
            # Set Global clipboard to none too
            self.global_clipboard_updated_qt.emit(None)
            self.table_files.paste_status = PasteStatus(status=PasteStatus.Status.Disabled)
        else:
            self.jobs_ctx.submit_job(
                ThreadSafeQtSignal(self, "move_success", QtToTrioJob),
                ThreadSafeQtSignal(self, "move_error", QtToTrioJob),
                _do_copy_files,
                workspace_fs=self.workspace_fs,
                current_directory=self.current_directory,
                files=self.clipboard.files,
                source_workspace=self.clipboard.source_workspace,
            )

    def _on_move_success(self, job):
        self.reset()

    def _on_move_error(self, job):
        if not getattr(job.exc, "params", None):
            return
        if isinstance(job.exc.params.get("last_exc", None), FSInvalidArgumentError):
            show_error(self, _("TEXT_FILE_FOLDER_MOVED_INTO_ITSELF_ERROR"))
        else:
            show_error(self, _("TEXT_FILE_PASTE_ERROR"))

        self.reset()

    def _on_copy_success(self, job):
        self.reset()

    def _on_copy_error(self, job):
        show_error(self, _("TEXT_FILE_PASTE_ERROR"))
        self.reset()

    def show_history(self):
        files = self.table_files.selected_files()
        if len(files) > 1:
            show_error(self, _("TEXT_FILE_HISTORY_MULTIPLE_FILES_SELECTED_ERROR"))
            return
        selected_path = self.current_directory / files[0].name
        FileHistoryWidget.show_modal(
            jobs_ctx=self.jobs_ctx,
            workspace_fs=self.workspace_fs,
            path=selected_path,
            reload_timestamped_signal=self.reload_timestamped_requested,
            update_version_list=self.update_version_list,
            close_version_list=self.close_version_list,
            core=self.core,
            parent=self,
            on_finished=None,
        )

    def rename_files(self):
        files = self.table_files.selected_files()
        if len(files) == 1:
            new_name = get_text_input(
                self,
                _("TEXT_FILE_RENAME_TITLE"),
                _("TEXT_FILE_RENAME_INSTRUCTIONS"),
                placeholder=_("TEXT_FILE_RENAME_PLACEHOLDER"),
                default_text=files[0].name,
                button_text=_("ACTION_FILE_RENAME"),
            )
            if not new_name:
                return
            self.jobs_ctx.submit_job(
                ThreadSafeQtSignal(self, "rename_success", QtToTrioJob),
                ThreadSafeQtSignal(self, "rename_error", QtToTrioJob),
                _do_rename,
                workspace_fs=self.workspace_fs,
                paths=[
                    (
                        self.current_directory / files[0].name,
                        self.current_directory / new_name,
                        files[0].uuid,
                    )
                ],
            )
        else:
            new_name = get_text_input(
                self,
                _("TEXT_FILE_RENAME_MULTIPLE_TITLE_count").format(count=len(files)),
                _("TEXT_FILE_RENAME_MULTIPLE_INSTRUCTIONS_count").format(count=len(files)),
                placeholder=_("TEXT_FILE_RENAME_MULTIPLE_PLACEHOLDER"),
                button_text=_("ACTION_FILE_RENAME_MULTIPLE"),
            )
            if not new_name:
                return

            self.jobs_ctx.submit_job(
                ThreadSafeQtSignal(self, "rename_success", QtToTrioJob),
                ThreadSafeQtSignal(self, "rename_error", QtToTrioJob),
                _do_rename,
                workspace_fs=self.workspace_fs,
                paths=[
                    (
                        self.current_directory / f.name,
                        self.current_directory
                        / "{}_{}{}".format(new_name, i, ".".join(pathlib.Path(f.name).suffixes)),
                        f.uuid,
                    )
                    for i, f in enumerate(files, 1)
                ],
            )

    def delete_files(self):
        files = self.table_files.selected_files()
        if len(files) == 1:
            result = ask_question(
                self,
                _("TEXT_FILE_DELETE_TITLE"),
                _("TEXT_FILE_DELETE_INSTRUCTIONS_name").format(name=files[0].name),
                [_("ACTION_FILE_DELETE"), _("ACTION_CANCEL")],
            )
        else:
            result = ask_question(
                self,
                _("TEXT_FILE_DELETE_MULTIPLE_TITLE_count").format(count=len(files)),
                _("TEXT_FILE_DELETE_MULTIPLE_INSTRUCTIONS_count").format(count=len(files)),
                [_("ACTION_FILE_DELETE_MULTIPLE"), _("ACTION_CANCEL")],
            )
        if result != _("ACTION_FILE_DELETE_MULTIPLE") and result != _("ACTION_FILE_DELETE"):
            return
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "delete_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "delete_error", QtToTrioJob),
            _do_delete,
            workspace_fs=self.workspace_fs,
            files=[(self.current_directory / f.name, f.type) for f in files],
        )

    def on_open_current_dir_clicked(self):
        self.open_file(None)

    def open_files(self):
        files = self.table_files.selected_files()
        if len(files) == 1:
            if not self.open_file(files[0][2]):
                show_error(self, _("TEXT_FILE_OPEN_ERROR_file").format(file=files[0][2]))
        else:
            result = ask_question(
                self,
                _("TEXT_FILE_OPEN_MULTIPLE_TITLE_count").format(count=len(files)),
                _("TEXT_FILE_OPEN_MULTIPLE_INSTRUCTIONS_count").format(count=len(files)),
                [_("ACTION_FILE_OPEN_MULTIPLE"), _("ACTION_CANCEL")],
            )
            if result != _("ACTION_FILE_OPEN_MULTIPLE"):
                return
            success = True
            for f in files:
                success &= self.open_file(f[2])
            if not success:
                show_error(self, _("TEXT_FILE_OPEN_MULTIPLE_ERROR"))

    def open_file(self, file_name):
        # The Qt thread should never hit the core directly.
        # Synchronous calls can run directly in the job system
        # as they won't block the Qt loop for long
        path = self.jobs_ctx.run_sync(
            self.core.mountpoint_manager.get_path_in_mountpoint,
            self.workspace_fs.workspace_id,
            self.current_directory / file_name if file_name else self.current_directory,
            self.workspace_fs.timestamp
            if isinstance(self.workspace_fs, WorkspaceFSTimestamped)
            else None,
        )
        return desktop.open_file(str(path))

    def item_activated(self, file_type, file_name):
        if file_type == FileType.ParentFolder:
            self.load(self.current_directory.parent)
        elif file_type == FileType.ParentWorkspace:
            self.back_clicked.emit()
        elif file_type == FileType.File:
            if not self.open_file(file_name):
                show_error(self, _("TEXT_FILE_OPEN_ERROR_file").format(file=file_name))
        elif file_type == FileType.Folder:
            self.load(self.current_directory / file_name)

    def reload(self):
        self.load(self.current_directory)

    def load(self, directory, default_selection=None):
        self.table_files.clear()
        self.spinner.show()
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "folder_stat_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "folder_stat_error", QtToTrioJob),
            _do_folder_stat,
            workspace_fs=self.workspace_fs,
            path=directory,
            default_selection=default_selection,
        )

    def import_all(self, files, total_size):
        assert not self.import_job

        wl = LoadingWidget(total_size=total_size + len(files))
        self.loading_dialog = GreyedDialog(wl, _("TEXT_FILE_IMPORT_LOADING_TITLE"), parent=self)
        wl.cancelled.connect(self.cancel_import)
        self.loading_dialog.show()

        self.import_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "import_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "import_error", QtToTrioJob),
            _do_import,
            workspace_fs=self.workspace_fs,
            files=files,
            total_size=total_size,
            progress_signal=ThreadSafeQtSignal(self, "import_progress", str, int),
        )

    def cancel_import(self):
        assert self.import_job
        assert self.loading_dialog

        self.import_job.cancel_and_join()

    def _on_import_progress(self, file_name, progress):
        if not self.loading_dialog:
            return
        self.loading_dialog.center_widget.set_progress(progress)
        self.loading_dialog.center_widget.set_current_file(file_name)

    def get_files(self, paths, dst_dir=None):
        files = []
        total_size = 0
        for path in paths:
            p = pathlib.Path(path)
            if dst_dir is not None:
                dst = dst_dir / p.name
            else:
                dst = self.current_directory / p.name
            files.append((p, dst))
            total_size += p.stat().st_size
        return files, total_size

    def get_folder(self, src, dst_dir=None):
        files = []
        total_size = 0
        if dst_dir is None:
            dst = self.current_directory / src.name
        else:
            dst = dst_dir / src.name
        for f in src.iterdir():
            if f.is_dir():
                new_files, new_size = self.get_folder(f, dst_dir=dst)
                files.extend(new_files)
                total_size += new_size
            elif f.is_file():
                new_dst = dst / f.name
                files.append((f, new_dst))
                total_size += f.stat().st_size
        return files, total_size

    def import_files_clicked(self):
        paths, x = QFileDialog.getOpenFileNames(
            self, _("TEXT_FILE_IMPORT_FILES"), self.default_import_path
        )
        if not paths:
            return
        files, total_size = self.get_files(paths)
        f = files[0][0]
        self.default_import_path = str(f.parent)
        self.import_all(files, total_size)

    def import_folder_clicked(self):
        path = QFileDialog.getExistingDirectory(
            self, _("TEXT_FILE_IMPORT_FOLDER"), self.default_import_path
        )
        if not path:
            return
        p = pathlib.Path(path)
        files, total_size = self.get_folder(p)
        self.default_import_path = str(p)
        self.import_all(files, total_size)

    def on_files_dropped(self, srcs, dst):
        files = []
        total_size = 0

        if dst == "..":
            dst_dir = self.current_directory.parent
        elif dst == ".":
            dst_dir = self.current_directory
        else:
            dst_dir = self.current_directory / dst

        for src in srcs:
            if src.is_dir():
                tmp_files, tmp_total_size = self.get_folder(src, dst_dir=dst_dir)
                files.extend(tmp_files)
                total_size += tmp_total_size
            elif src.is_file():
                tmp_files, tmp_total_size = self.get_files([src], dst_dir=dst_dir)
                files.extend(tmp_files)
                total_size += tmp_total_size
        self.import_all(files, total_size)

    def on_file_moved(self, src, dst):
        src_path = self.current_directory / src
        dst_path = ""
        if dst == "..":
            dst_path = self.current_directory.parent / src
        else:
            dst_path = self.current_directory / dst / src
        self.jobs_ctx.run(self.workspace_fs.move, src_path, dst_path)

    def filter_files(self, pattern):
        pattern = pattern.lower()
        for i in range(self.table_files.rowCount()):
            file_type = self.table_files.item(i, 0).data(TYPE_DATA_INDEX)
            name_item = self.table_files.item(i, 1)
            if file_type != FileType.ParentFolder and file_type != FileType.ParentWorkspace:
                if pattern not in name_item.text().lower():
                    self.table_files.setRowHidden(i, True)
                else:
                    self.table_files.setRowHidden(i, False)

    def create_folder_clicked(self):
        folder_name = get_text_input(
            self,
            _("TEXT_FILE_CREATE_FOLDER_TITLE"),
            _("TEXT_FILE_CREATE_FOLDER_INSTRUCTIONS"),
            placeholder=_("TEXT_FILE_CREATE_FOLDER_PLACEHOLDER"),
            button_text=_("ACTION_FILE_CREATE_FOLDER"),
        )
        if not folder_name:
            return

        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "folder_create_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "folder_create_error", QtToTrioJob),
            _do_folder_create,
            workspace_fs=self.workspace_fs,
            path=self.current_directory / folder_name,
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_files()

    def _on_rename_success(self, job):
        self.reset()

    def _on_rename_error(self, job):
        if not getattr(job.exc, "params", None):
            return
        if job.exc.params.get("multi"):
            show_error(self, _("TEXT_FILE_RENAME_MULTIPLE_ERROR"), exception=job.exc)
        else:
            show_error(self, _("TEXT_FILE_RENAME_ERROR"), exception=job.exc)

    def _on_delete_success(self, job):
        self.reset()

    def _on_delete_error(self, job):
        if not getattr(job.exc, "params", None):
            return
        if job.exc.params.get("multi"):
            show_error(self, _("TEXT_FILE_DELETE_MULTIPLE_ERROR"), exception=job.exc)
        else:
            show_error(self, _("TEXT_FILE_DELETE_ERROR"), exception=job.exc)

    def _on_folder_stat_success(self, job):
        self.current_directory, self.current_directory_uuid, files_stats, default_selection = (
            job.ret
        )
        self.table_files.clear()
        self.spinner.hide()
        old_sort = self.table_files.horizontalHeader().sortIndicatorSection()
        old_order = self.table_files.horizontalHeader().sortIndicatorOrder()
        self.table_files.setSortingEnabled(False)
        if self.current_directory == FsPath("/"):
            self.table_files.add_parent_workspace()
        else:
            self.table_files.add_parent_folder()
        file_found = False
        for path, stats in files_stats.items():
            # Must check first given inconsistent stats result are missing fields
            if stats["type"] == "inconsistency":
                self.table_files.add_inconsistency(str(path), stats["id"])
                continue
            selected = False
            confined = bool(stats["confinement_point"])
            if default_selection and str(path) == default_selection:
                selected = True
                file_found = True
            if stats["type"] == "folder":
                self.table_files.add_folder(
                    str(path), stats["id"], not stats["need_sync"], confined, selected
                )
            else:
                self.table_files.add_file(
                    str(path),
                    stats["id"],
                    stats["size"],
                    stats["created"],
                    stats["updated"],
                    not stats["need_sync"],
                    confined,
                    selected,
                )
        self.table_files.sortItems(old_sort, old_order)
        self.table_files.setSortingEnabled(True)
        if self.line_edit_search.text():
            self.filter_files(self.line_edit_search.text())
        if default_selection and not file_found:
            show_error(self, _("TEXT_FILE_GOTO_LINK_NOT_FOUND"))
        workspace_name = self.jobs_ctx.run_sync(self.workspace_fs.get_workspace_name)
        self.folder_changed.emit(str(workspace_name), str(self.current_directory))

    def _on_folder_stat_error(self, job):
        self.table_files.clear()
        self.spinner.hide()
        if isinstance(job.exc, FSFileNotFoundError):
            show_error(self, _("TEXT_FILE_FOLDER_NOT_FOUND"))
            self.table_files.add_parent_workspace()
            return
        if self.current_directory == FsPath("/"):
            self.table_files.add_parent_workspace()
        else:
            self.table_files.add_parent_folder()

    def _on_folder_create_success(self, job):
        pass

    def _on_folder_create_error(self, job):
        if job.status == "already-exists":
            show_error(self, _("TEXT_FILE_FOLDER_CREATE_ERROR_ALREADY_EXISTS"))
        else:
            show_error(self, _("TEXT_FILE_FOLDER_CREATE_ERROR_UNKNOWN"))

    def _on_import_success(self):
        assert self.loading_dialog
        self.loading_dialog.hide()
        self.loading_dialog.setParent(None)
        self.loading_dialog = None
        self.import_job = None

    def _on_import_error(self):
        def _display_import_error(file_count, exceptions=None):
            if exceptions and all(isinstance(exc, PermissionError) for exc in exceptions):
                if file_count and file_count == 1:
                    show_error(
                        self, _("TEXT_FILE_IMPORT_ONE_PERMISSION_ERROR"), exception=exceptions[0]
                    )
                else:
                    show_error(
                        self,
                        _("TEXT_FILE_IMPORT_MULTIPLE_PERMISSION_ERROR"),
                        exception=exceptions[0],
                    )
            else:
                if file_count and file_count == 1:
                    show_error(
                        self,
                        _("TEXT_FILE_IMPORT_ONE_ERROR"),
                        exception=exceptions[0] if exceptions else None,
                    )
                else:
                    show_error(
                        self,
                        _("TEXT_FILE_IMPORT_MULTIPLE_ERROR"),
                        exception=exceptions[0] if exceptions else None,
                    )

        assert self.loading_dialog

        if hasattr(self.import_job.exc, "status") and self.import_job.exc.status == "cancelled":
            self.jobs_ctx.submit_job(
                ThreadSafeQtSignal(self, "delete_success", QtToTrioJob),
                ThreadSafeQtSignal(self, "delete_error", QtToTrioJob),
                _do_delete,
                workspace_fs=self.workspace_fs,
                files=[(self.import_job.exc.params["last_file"], FileType.File)],
                silent=True,
            )
        else:
            _display_import_error(
                file_count=self.import_job.exc.params.get("file_count", 0),
                exceptions=self.import_job.exc.params.get("exceptions", None),
            )
        self.loading_dialog.hide()
        self.loading_dialog.setParent(None)
        self.loading_dialog = None
        self.import_job = None

    def _on_fs_entry_synced_trio(self, event, id, workspace_id=None):
        self.fs_synced_qt.emit(event, id)

    def _on_fs_entry_updated_trio(self, event, workspace_id=None, id=None):
        assert id is not None
        if workspace_id is None or (
            self.workspace_fs is not None and workspace_id == self.workspace_fs.workspace_id
        ):
            self.fs_updated_qt.emit(event, id)

    def _on_entry_downsynced_trio(self, event, workspace_id=None, id=None):
        self.entry_downsynced_qt.emit(workspace_id, id)

    def _on_entry_downsynced_qt(self, workspace_id, id):
        if not self.workspace_fs:
            return
        ws_id = self.workspace_fs.workspace_id
        if ws_id != workspace_id:
            return
        if id == self.current_directory_uuid:
            if not self.update_timer.isActive():
                self.update_timer.start()
                self.reload()

    def _on_fs_synced_qt(self, event, uuid):
        if not self.workspace_fs:
            return

        if self.current_directory_uuid == uuid:
            return

        for i in range(1, self.table_files.rowCount()):
            item = self.table_files.item(i, 0)
            if item and item.data(UUID_DATA_INDEX) == uuid:
                if (
                    item.data(TYPE_DATA_INDEX) == FileType.File
                    or item.data(TYPE_DATA_INDEX) == FileType.Folder
                ):
                    item.confined = False
                    item.is_synced = True

    def _on_fs_updated_qt(self, event, uuid):
        if not self.workspace_fs:
            return

        if self.current_directory_uuid == uuid or self.table_files.has_file(uuid):
            if not self.update_timer.isActive():
                self.update_timer.start()
                self.reload()

    def _on_sharing_updated_trio(self, event, new_entry, previous_entry):
        self.sharing_updated_qt.emit(new_entry, previous_entry)

    def _on_sharing_updated_qt(self, new_entry, previous_entry):
        if new_entry is None or new_entry.role is None:
            # Sharing revoked
            show_error(
                self, _("TEXT_FILE_SHARING_REVOKED_workspace").format(workspace=previous_entry.name)
            )
            self.back_clicked.emit()

        elif previous_entry is not None and previous_entry.role is not None:
            self.current_user_role = new_entry.role
            self.label_role.setText(get_role_translation(self.current_user_role))
            if (
                previous_entry.role != WorkspaceRole.READER
                and new_entry.role == WorkspaceRole.READER
            ):
                show_error(self, _("TEXT_FILE_SHARING_DEMOTED_TO_READER"))

    def _on_reload_timestamped_requested(
        self, timestamp, path, file_type, open_after_load, close_after_remount, reload_after_remount
    ):
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "reload_timestamped_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "reload_timestamped_error", QtToTrioJob),
            _do_remount_timestamped,
            mountpoint_manager=self.core.mountpoint_manager,
            workspace_fs=self.workspace_fs,
            timestamp=timestamp,
            path=path if path is not None else self.current_directory,
            file_type=file_type,
            open_after_load=open_after_load,
            close_after_load=close_after_remount,
            reload_after_remount=reload_after_remount,
        )

    def _on_reload_timestamped_success(self, job):
        (
            workspace_fs,
            path,
            file_type,
            open_after_load,
            close_after_load,
            reload_after_remount,
        ) = job.ret
        self.set_workspace_fs(workspace_fs, path.parent if file_type == FileType.File else path)
        # TODO : Select element if possible?
        if close_after_load:
            self.close_version_list.emit()
        if reload_after_remount:
            self.update_version_list.emit(self.workspace_fs, path)
        if open_after_load:
            self.open_file(path.name)

    def _on_reload_timestamped_error(self, job):
        raise job.exc
