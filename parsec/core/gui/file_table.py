# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from collections import namedtuple
import pendulum
import pathlib
from enum import IntEnum
from sys import platform

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QColor, QKeySequence
from PyQt5.QtWidgets import (
    QTableWidget,
    QHeaderView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
    QMenu,
    QTableWidgetSelectionRange,
    QGraphicsDropShadowEffect,
)

from parsec.core.types import WorkspaceRole, FsPath

from parsec.core.gui.lang import translate as _, format_datetime
from parsec.core.gui.file_items import (
    FileTableItem,
    CustomTableItem,
    FolderTableItem,
    InconsistencyTableItem,
    FileType,
    NAME_DATA_INDEX,
    TYPE_DATA_INDEX,
    UUID_DATA_INDEX,
    COPY_STATUS_DATA_INDEX,
)
from parsec.core.gui.custom_widgets import Pixmap
from parsec.core.gui.custom_dialogs import show_error
from parsec.core.gui.file_size import get_filesize


class PasteStatus:
    class Status(IntEnum):
        Disabled = 1
        Enabled = 2

    def __init__(self, status, source_workspace=None):
        self.source_workspace = source_workspace
        self.status = status


class Column(IntEnum):
    ICON = 0
    NAME = 1
    CREATED = 2
    UPDATED = 3
    SIZE = 4


class ItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        view_option = QStyleOptionViewItem(option)
        view_option.decorationAlignment |= Qt.AlignHCenter
        # Qt tries to be nice and adds a lovely background color
        # on the focused item. Since we select items by rows and not
        # individually, we don't want that, so we remove the focus
        if option.state & QStyle.State_HasFocus:
            view_option.state &= ~QStyle.State_HasFocus
        if index.data(COPY_STATUS_DATA_INDEX):
            view_option.font.setItalic(True)
        super().paint(painter, view_option, index)


class FileTable(QTableWidget):
    FIXED_COL_SIZE = 560
    NAME_COL_MIN_SIZE = 150

    file_moved = pyqtSignal(str, str)
    item_activated = pyqtSignal(FileType, str)
    files_dropped = pyqtSignal(list, str)
    delete_clicked = pyqtSignal()
    rename_clicked = pyqtSignal()
    open_clicked = pyqtSignal()
    show_history_clicked = pyqtSignal()
    paste_clicked = pyqtSignal()
    cut_clicked = pyqtSignal()
    copy_clicked = pyqtSignal()
    file_path_clicked = pyqtSignal()
    open_current_dir_clicked = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.previous_selection = []
        self.setColumnCount(len(Column))
        self.config = None

        h_header = self.horizontalHeader()
        h_header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        for col in Column:
            h_header.setSectionResizeMode(col, QHeaderView.Fixed)

        self.setColumnWidth(Column.ICON, 50)
        self.setColumnWidth(
            Column.NAME,
            max(self.size().width() - FileTable.FIXED_COL_SIZE, FileTable.NAME_COL_MIN_SIZE),
        )
        self.setColumnWidth(Column.CREATED, 200)
        self.setColumnWidth(Column.UPDATED, 200)
        self.setColumnWidth(Column.SIZE, 100)

        v_header = self.verticalHeader()
        v_header.setSectionResizeMode(QHeaderView.Fixed)
        v_header.setDefaultSectionSize(48)
        self.setItemDelegate(ItemDelegate())
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.itemSelectionChanged.connect(self.change_selection)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.cellDoubleClicked.connect(self.item_double_clicked)
        self.cellClicked.connect(self.item_clicked)
        self.current_user_role = WorkspaceRole.OWNER
        self.paste_status = PasteStatus(status=PasteStatus.Status.Disabled)
        effect = QGraphicsDropShadowEffect(self)
        effect.setColor(QColor(34, 34, 34, 25))
        effect.setBlurRadius(8)
        effect.setXOffset(0)
        effect.setYOffset(2)
        self.setGraphicsEffect(effect)

    @property
    def current_user_role(self):
        return self._current_user_role

    @current_user_role.setter
    def current_user_role(self, role):
        self._current_user_role = role

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setColumnWidth(
            Column.NAME,
            max(event.size().width() - FileTable.FIXED_COL_SIZE, FileTable.NAME_COL_MIN_SIZE),
        )

    def set_rows_cut(self, rows):
        for row in range(self.rowCount()):
            for col in Column:
                item = self.item(row, col)
                item.setData(COPY_STATUS_DATA_INDEX, row in rows)

    def keyReleaseEvent(self, event):
        if not self.is_read_only():
            if event.matches(QKeySequence.Copy):
                self.copy_clicked.emit()
            elif event.matches(QKeySequence.Cut):
                self.cut_clicked.emit()
            elif event.matches(QKeySequence.Paste):
                if self.paste_status.status == PasteStatus.Status.Enabled:
                    self.paste_clicked.emit()

    def select_rows(self, select_rows):
        for row in range(1, self.rowCount()):
            item = self.item(row, Column.NAME)
            if item:
                if row in select_rows:
                    item.setSelected(True)
                else:
                    item.setSelected(False)

    def selected_files(self):
        SelectedFile = namedtuple("SelectedFile", ["row", "type", "name", "uuid"])

        files = []
        for r in self.selectedRanges():
            for row in range(r.topRow(), r.bottomRow() + 1):
                item = self.item(row, Column.NAME)
                files.append(
                    SelectedFile(
                        row,
                        item.data(TYPE_DATA_INDEX),
                        item.data(NAME_DATA_INDEX),
                        item.data(UUID_DATA_INDEX),
                    )
                )
        return files

    def has_file(self, uuid):
        return any(
            uuid == self.item(row, Column.NAME).data(UUID_DATA_INDEX)
            for row in range(self.rowCount())
            if self.item(row, Column.NAME)
        )

    def is_read_only(self):
        return self.current_user_role == WorkspaceRole.READER

    def show_context_menu(self, pos):
        global_pos = self.mapToGlobal(pos)

        selected = self.selected_files()
        menu = QMenu(self)

        if platform == "darwin":
            action = menu.addAction(_("ACTION_FILE_OPEN_CURRENT_DIRECTORY_MAC"))
        else:
            action = menu.addAction(_("ACTION_FILE_OPEN_CURRENT_DIRECTORY"))
        action.triggered.connect(self.open_current_dir_clicked.emit)
        if len(selected):
            action = menu.addAction(_("ACTION_FILE_MENU_OPEN"))
            action.triggered.connect(self.open_clicked.emit)
            if not self.is_read_only():
                action = menu.addAction(_("ACTION_FILE_MENU_RENAME"))
                action.triggered.connect(self.rename_clicked.emit)
                action = menu.addAction(_("ACTION_FILE_MENU_DELETE"))
                action.triggered.connect(self.delete_clicked.emit)
                action = menu.addAction(_("ACTION_FILE_MENU_COPY"))
                action.triggered.connect(self.copy_clicked.emit)
                action = menu.addAction(_("ACTION_FILE_MENU_CUT"))
                action.triggered.connect(self.cut_clicked.emit)
        if len(selected) == 1:
            action = menu.addAction(_("ACTION_FILE_MENU_SHOW_FILE_HISTORY"))
            action.triggered.connect(self.show_history_clicked.emit)
            action = menu.addAction(_("ACTION_FILE_MENU_GET_FILE_LINK"))
            action.triggered.connect(self.file_path_clicked.emit)
        if not self.is_read_only():
            if self.paste_status.source_workspace:
                action = menu.addAction(
                    _("ACTION_FILE_MENU_PASTE_FROM_OTHER_WORKSPACE_source").format(
                        source=self.paste_status.source_workspace
                    )
                )
            else:
                action = menu.addAction(_("ACTION_FILE_MENU_PASTE"))
            action.triggered.connect(self.paste_clicked.emit)
            if self.paste_status.status == PasteStatus.Status.Disabled:
                action.setDisabled(True)
        menu.exec_(global_pos)

    def item_double_clicked(self, row, column):
        item = self.item(row, Column.NAME)
        file_type = item.data(TYPE_DATA_INDEX)
        try:
            self.item_activated.emit(file_type, item.data(NAME_DATA_INDEX))
        except AttributeError:
            # This can happen when updating the list: double click event gets processed after
            # the item has been removed.
            pass

    def item_clicked(self, row, column):
        item = self.item(row, Column.NAME)
        file_type = item.data(TYPE_DATA_INDEX)
        try:
            if file_type == FileType.ParentFolder or file_type == FileType.ParentWorkspace:
                self.item_activated.emit(file_type, item.data(NAME_DATA_INDEX))
            pass
        except AttributeError:
            pass

    def clear(self):
        self.clearContents()
        self.setRowCount(0)
        self.previous_selection = []

    def change_selection(self):
        selected = self.selectedItems()
        for item in self.previous_selection:
            if item.column() == Column.ICON:
                file_type = item.data(TYPE_DATA_INDEX)
                if file_type == FileType.ParentWorkspace or file_type == FileType.ParentFolder:
                    p = Pixmap(":/icons/images/material/arrow_upward.svg")
                    p.replace_color(QColor(0, 0, 0), QColor(0x99, 0x99, 0x99))
                    item.setIcon(QIcon(p))
                elif file_type == FileType.File or file_type == FileType.Folder:
                    item.switch_icon()
        for item in selected:
            if item.column() == Column.ICON:
                file_type = item.data(TYPE_DATA_INDEX)
                if file_type == FileType.ParentWorkspace or file_type == FileType.ParentFolder:
                    p = Pixmap(":/icons/images/material/arrow_upward.svg")
                    p.replace_color(QColor(0, 0, 0), QColor(255, 255, 255))
                    item.setIcon(QIcon(p))
                elif file_type == FileType.File or file_type == FileType.Folder:
                    item.switch_icon()
        self.previous_selection = selected

    def update_file_list(self, current_directory, files_stats, default_selection):
        selection_found = False
        if self.rowCount() == 0:
            if current_directory == FsPath("/"):
                self.add_parent_workspace()
            else:
                self.add_parent_folder()

        to_remove = []
        for row in range(1, self.rowCount()):
            name_item = self.item(row, Column.NAME)
            if not name_item:
                continue
            uuid = name_item.data(UUID_DATA_INDEX)
            if uuid in files_stats:
                name = str(files_stats[uuid]["name"])
                # The file is in the list and also in stats, we update it
                if default_selection and name == default_selection:
                    selected = True
                    selection_found = True
                stats = files_stats[uuid]
                icon_item = self.item(row, Column.ICON)
                icon_item.is_synced = not stats["need_sync"]
                icon_item.is_confined = bool(stats["confinement_point"])
                name_item.setData(NAME_DATA_INDEX, name)
                name_item.setToolTip("\n".join(name[i : i + 64] for i in range(0, len(name), 64)))
                name_item.setText(name)

                if stats["type"] == "file":
                    created_item = self.item(row, Column.CREATED)
                    created_item.setText(format_datetime(stats["created"]))
                    created_item.setData(NAME_DATA_INDEX, stats["created"])

                    updated_item = self.item(row, Column.UPDATED)
                    updated_item.setText(format_datetime(stats["updated"]))
                    updated_item.setData(NAME_DATA_INDEX, stats["updated"])

                    size_item = self.item(row, Column.SIZE)
                    size_item.setText(get_filesize(stats["size"]))
                    size_item.setData(NAME_DATA_INDEX, stats["size"])

                del files_stats[uuid]
            else:
                # The file is in the list but not in stats, we mark it for removal.
                # We cannot immediately remove it since we're iterating over rowCount()
                to_remove.append(uuid)

        # Remove files marked for removal
        # We doin' it old school because rowCount will change as we remove rows
        row = 1
        while row < self.rowCount():
            if self.item(row, Column.NAME).data(UUID_DATA_INDEX) in to_remove:
                self.removeRow(row)
            else:
                row += 1

        # Add additional files that are in stats but not in the list
        for uuid, stats in files_stats.items():
            selected = False
            confined = bool(stats["confinement_point"])
            if default_selection and str(stats["name"]) == default_selection:
                selected = True
                selection_found = True
            if stats["type"] == "inconsistency":
                self.add_inconsistency(str(stats["name"]), uuid)
            elif stats["type"] == "folder":
                self.add_folder(
                    str(stats["name"]), uuid, not stats["need_sync"], confined, selected
                )
            else:
                self.add_file(
                    str(stats["name"]),
                    uuid,
                    stats["size"],
                    stats["created"],
                    stats["updated"],
                    not stats["need_sync"],
                    confined,
                    selected,
                )
        return selection_found

    def add_parent_folder(self):
        row_idx = self.rowCount()
        self.insertRow(row_idx)
        items = []
        p = Pixmap(":/icons/images/material/arrow_upward.svg")
        p.replace_color(QColor(0, 0, 0), QColor(0x99, 0x99, 0x99))
        items.append(CustomTableItem(QIcon(p), ""))
        items.append(CustomTableItem(_("TEXT_FILE_TREE_PARENT_FOLDER")))
        items.append(CustomTableItem())
        items.append(CustomTableItem())
        items.append(CustomTableItem())

        for col, item in zip(Column, items):
            item.setData(NAME_DATA_INDEX, "")
            item.setData(TYPE_DATA_INDEX, FileType.ParentFolder)
            item.setData(UUID_DATA_INDEX, None)
            item.setFlags(Qt.ItemIsEnabled)
            self.setItem(row_idx, col, item)

    def add_parent_workspace(self):
        row_idx = self.rowCount()
        self.insertRow(row_idx)
        items = []
        p = Pixmap(":/icons/images/material/arrow_upward.svg")
        p.replace_color(QColor(0, 0, 0), QColor(0x99, 0x99, 0x99))
        items.append(CustomTableItem(QIcon(p), ""))
        items.append(CustomTableItem(_("TEXT_FILE_TREE_PARENT_WORKSPACE")))
        items.append(CustomTableItem())
        items.append(CustomTableItem())
        items.append(CustomTableItem())

        for col, item in zip(Column, items):
            item.setData(NAME_DATA_INDEX, "")
            item.setData(TYPE_DATA_INDEX, FileType.ParentWorkspace)
            item.setData(UUID_DATA_INDEX, None)
            item.setFlags(Qt.ItemIsEnabled)
            self.setItem(row_idx, col, item)

    def add_folder(self, folder_name, uuid, is_synced, is_confined, selected=False):
        if is_confined and not self.config.gui_show_confined:
            return
        row_idx = self.rowCount()
        self.insertRow(row_idx)
        item = FolderTableItem(is_synced, is_confined)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.ICON, item)
        item = CustomTableItem(folder_name)
        item.setData(NAME_DATA_INDEX, folder_name)
        item.setToolTip("\n".join(folder_name[i : i + 64] for i in range(0, len(folder_name), 64)))
        item.setData(TYPE_DATA_INDEX, FileType.Folder)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.NAME, item)
        item = CustomTableItem()
        item.setData(NAME_DATA_INDEX, pendulum.datetime(1970, 1, 1))
        item.setData(TYPE_DATA_INDEX, FileType.Folder)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.CREATED, item)
        item = CustomTableItem()
        item.setData(NAME_DATA_INDEX, pendulum.datetime(1970, 1, 1))
        item.setData(TYPE_DATA_INDEX, FileType.Folder)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.UPDATED, item)
        item = CustomTableItem()
        item.setData(NAME_DATA_INDEX, -1)
        item.setData(TYPE_DATA_INDEX, FileType.Folder)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.SIZE, item)
        if selected:
            self.setRangeSelected(
                QTableWidgetSelectionRange(row_idx, 0, row_idx, len(Column) - 1), True
            )

    def add_file(
        self,
        file_name,
        uuid,
        file_size,
        created_on,
        updated_on,
        is_synced,
        is_confined,
        selected=False,
    ):
        if is_confined and not self.config.gui_show_confined:
            return
        row_idx = self.rowCount()
        self.insertRow(row_idx)
        item = FileTableItem(is_synced, is_confined, file_name)
        item.setData(NAME_DATA_INDEX, 1)
        item.setData(TYPE_DATA_INDEX, FileType.File)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.ICON, item)
        item = CustomTableItem(file_name)
        item.setToolTip("\n".join(file_name[i : i + 64] for i in range(0, len(file_name), 64)))
        item.setData(NAME_DATA_INDEX, file_name)
        item.setData(TYPE_DATA_INDEX, FileType.File)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.NAME, item)
        item = CustomTableItem(format_datetime(created_on))
        item.setData(NAME_DATA_INDEX, created_on)
        item.setData(TYPE_DATA_INDEX, FileType.File)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.CREATED, item)
        item = CustomTableItem(format_datetime(updated_on))
        item.setData(NAME_DATA_INDEX, updated_on)
        item.setData(TYPE_DATA_INDEX, FileType.File)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.UPDATED, item)
        item = CustomTableItem(get_filesize(file_size))
        item.setData(NAME_DATA_INDEX, file_size)
        item.setData(TYPE_DATA_INDEX, FileType.File)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.SIZE, item)
        if selected:
            self.setRangeSelected(
                QTableWidgetSelectionRange(row_idx, 0, row_idx, len(Column) - 1), True
            )

    def add_inconsistency(self, file_name, uuid):
        inconsistency_color = QColor(255, 144, 155)
        row_idx = self.rowCount()
        self.insertRow(row_idx)
        item = InconsistencyTableItem(False, False)
        item.setData(NAME_DATA_INDEX, 1)
        item.setData(TYPE_DATA_INDEX, FileType.Inconsistency)
        item.setData(UUID_DATA_INDEX, uuid)
        item.setBackground(inconsistency_color)
        self.setItem(row_idx, Column.ICON, item)
        item = CustomTableItem(file_name)
        item.setToolTip("\n".join(file_name[i : i + 64] for i in range(0, len(file_name), 64)))
        item.setData(NAME_DATA_INDEX, file_name)
        item.setData(TYPE_DATA_INDEX, FileType.Inconsistency)
        item.setData(UUID_DATA_INDEX, uuid)
        item.setBackground(inconsistency_color)
        self.setItem(row_idx, Column.NAME, item)
        item = CustomTableItem()
        item.setData(NAME_DATA_INDEX, pendulum.datetime(1970, 1, 1))
        item.setData(TYPE_DATA_INDEX, FileType.Inconsistency)
        item.setBackground(inconsistency_color)
        item.setData(UUID_DATA_INDEX, uuid)
        self.setItem(row_idx, Column.CREATED, item)
        item = CustomTableItem()
        item.setData(NAME_DATA_INDEX, pendulum.datetime(1970, 1, 1))
        item.setData(TYPE_DATA_INDEX, FileType.Inconsistency)
        item.setData(UUID_DATA_INDEX, uuid)
        item.setBackground(inconsistency_color)
        self.setItem(row_idx, Column.UPDATED, item)
        item = CustomTableItem(-1)
        item.setData(NAME_DATA_INDEX, -1)
        item.setData(TYPE_DATA_INDEX, FileType.Inconsistency)
        item.setData(UUID_DATA_INDEX, uuid)
        item.setBackground(inconsistency_color)
        self.setItem(row_idx, Column.SIZE, item)

    def dragEnterEvent(self, event):
        event.accept()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            if event.source() != self:
                event.ignore()
                return
            target_row = self.indexAt(event.pos()).row()
            item = self.item(target_row, Column.ICON)
            if not item:
                return
            file_type = item.data(TYPE_DATA_INDEX)
            if file_type == FileType.ParentFolder or file_type == FileType.Folder:
                event.accept()
            else:
                event.ignore()

    def dropEvent(self, event):
        if self.is_read_only():
            show_error(self, _("TEXT_FILE_DROP_WORKSPACE_IS_READ_ONLY"))
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.accept()
            target_row = self.indexAt(event.pos()).row()
            target_item = self.item(target_row, Column.ICON)
            files = [pathlib.Path(url.toLocalFile()) for url in event.mimeData().urls()]
            if not target_item:
                self.files_dropped.emit(files, ".")
                return
            target_type = target_item.data(TYPE_DATA_INDEX)
            if target_type == FileType.File or target_type == FileType.ParentWorkspace:
                self.files_dropped.emit(files, ".")
            elif target_type == FileType.ParentFolder:
                self.files_dropped.emit(files, "..")
            elif target_type == FileType.Folder:
                self.files_dropped.emit(files, self.item(target_row, 1).text())
        else:
            if event.source() != self:
                return
            target_row = self.indexAt(event.pos()).row()
            rows = set([i.row for i in self.selected_files() if i.row != target_row])
            if not rows:
                return
            if not self.item(target_row, Column.ICON):
                return
            file_type = self.item(target_row, Column.ICON).data(TYPE_DATA_INDEX)
            target_name = self.item(target_row, Column.NAME).text()

            if file_type != FileType.ParentFolder and file_type != FileType.Folder:
                return
            for row in rows:
                file_name = self.item(row, Column.NAME).text()
                if file_type == FileType.ParentFolder:
                    self.file_moved.emit(file_name, "..")
                else:
                    self.file_moved.emit(file_name, target_name)
            for row in rows:
                self.removeRow(row)
            event.accept()
