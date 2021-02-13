# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import pathlib
import pytest
from functools import partial
from PyQt5 import QtCore, QtWidgets, QtGui

from parsec.core.types import WorkspaceRole

from parsec.core.gui.lang import translate as _
from parsec.core.gui.file_items import FileType, NAME_DATA_INDEX, TYPE_DATA_INDEX
from parsec.test_utils import create_inconsistent_workspace

from tests.common import customize_fixtures


@pytest.fixture
def temp_dir(tmpdir):
    temp = pathlib.Path(tmpdir) / "temp_dir"
    pathlib.Path(temp / "dir1/dir11").mkdir(parents=True)
    pathlib.Path(temp / "dir1/dir12").mkdir(parents=True)
    pathlib.Path(temp / "dir2/dir21").mkdir(parents=True)
    pathlib.Path(temp / "file01.txt").write_text("Content file01")
    pathlib.Path(temp / "file02.txt").write_text("Content file02")
    pathlib.Path(temp / "dir1/dir11" / "file.txt").write_text("Content file111")
    pathlib.Path(temp / "dir2" / "file2.txt").write_text("Content file2")
    return temp


@pytest.fixture
async def logged_gui_with_workspace(
    aqtbot,
    logged_gui,
    gui_factory,
    autoclose_dialog,
    core_config,
    alice,
    running_backend,
    monkeypatch,
    fixtures_customization,
):
    # Logged as bob (i.e. standard profile) by default
    if fixtures_customization.get("logged_gui_create_two_workspaces", True):
        workspaces_nb = 2
    else:
        workspaces_nb = 1

    central_widget = logged_gui.test_get_central_widget()
    assert central_widget is not None

    wk_widget = logged_gui.test_get_workspaces_widget()
    async with aqtbot.wait_signal(wk_widget.list_success):
        pass

    add_button = wk_widget.button_add_workspace
    assert add_button is not None

    for index in range(workspaces_nb):
        workspace_name = "Workspace"
        workspace_name = workspace_name + str(index + 1) if index > 0 else workspace_name
        monkeypatch.setattr(
            "parsec.core.gui.workspaces_widget.get_text_input",
            lambda *args, **kwargs: (workspace_name),
        )

        async with aqtbot.wait_signals(
            [wk_widget.create_success, wk_widget.list_success, wk_widget.mountpoint_started],
            timeout=2000,
        ):
            await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

    def workspace_button_ready():
        assert wk_widget.layout_workspaces.count() == workspaces_nb
        wk_button = wk_widget.layout_workspaces.itemAt(0).widget()
        assert not isinstance(wk_button, QtWidgets.QLabel)

    await aqtbot.wait_until(workspace_button_ready)
    wk_button = wk_widget.layout_workspaces.itemAt(0).widget()
    assert wk_button.name == "Workspace"

    async with aqtbot.wait_signal(wk_widget.load_workspace_clicked):
        await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)

    yield logged_gui


@pytest.fixture
async def logged_gui_with_files(
    aqtbot, logged_gui_with_workspace, running_backend, monkeypatch, temp_dir
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getOpenFileNames",
        classmethod(
            lambda *args, **kwargs: ([temp_dir / "file01.txt", temp_dir / "file02.txt"], True)
        ),
    )

    async with aqtbot.wait_signals(
        [w_f.button_import_files.clicked, w_f.import_success], timeout=3000
    ):
        await aqtbot.mouse_click(w_f.button_import_files, QtCore.Qt.LeftButton)

    add_button = w_f.button_create_folder
    assert add_button is not None

    monkeypatch.setattr(
        "parsec.core.gui.files_widget.get_text_input", lambda *args, **kwargs: ("dir1")
    )
    async with aqtbot.wait_signal(w_f.folder_create_success):
        await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

    # Wait until the file widget is refreshed
    while w_f.table_files.rowCount() < 4:
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 4
    assert w_f.table_files.item(1, 1).text() == "dir1"
    assert w_f.table_files.item(2, 1).text() == "file01.txt"
    assert w_f.table_files.item(3, 1).text() == "file02.txt"

    yield logged_gui_with_workspace


async def create_directories(logged_gui_with_workspace, aqtbot, monkeypatch, dir_names):
    w_f = logged_gui_with_workspace.test_get_files_widget()
    assert w_f is not None

    add_button = w_f.button_create_folder

    for dir_name in dir_names:
        monkeypatch.setattr(
            "parsec.core.gui.files_widget.get_text_input", lambda *args, **kwargs: (dir_name)
        )
        async with aqtbot.wait_signal(w_f.folder_create_success):
            await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

    async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
        pass


@pytest.mark.gui
@pytest.mark.trio
async def test_list_files(aqtbot, running_backend, logged_gui_with_workspace):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass

    central_widget = logged_gui_with_workspace.test_get_central_widget()
    assert central_widget.label_title2.text() == "Workspace"
    assert central_widget.label_title3.text() == "/"

    assert w_f.table_files.rowCount() == 1
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace


@pytest.mark.gui
@pytest.mark.trio
async def test_create_dir(aqtbot, running_backend, logged_gui_with_workspace, monkeypatch):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(logged_gui_with_workspace, aqtbot, monkeypatch, ["Dir1"])

    assert w_f.table_files.rowCount() == 2
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace
        assert w_f.table_files.item(1, i).data(TYPE_DATA_INDEX) == FileType.Folder
    assert w_f.table_files.item(1, 1).text() == "Dir1"


@pytest.mark.gui
@pytest.mark.trio
async def test_create_dir_already_exists(
    aqtbot, running_backend, logged_gui_with_workspace, monkeypatch, autoclose_dialog
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    add_button = w_f.button_create_folder

    monkeypatch.setattr(
        "parsec.core.gui.files_widget.get_text_input", lambda *args, **kwargs: ("Dir1")
    )
    async with aqtbot.wait_signals(
        [w_f.folder_create_success, w_f.folder_stat_success, w_f.fs_synced_qt], timeout=3000
    ):
        await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

    assert w_f.table_files.rowCount() == 2
    assert w_f.table_files.item(1, 1).text() == "Dir1"

    async with aqtbot.wait_signal(w_f.folder_create_error):
        await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

    assert w_f.table_files.rowCount() == 2

    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "A folder with the same name already exists."


@pytest.mark.gui
@pytest.mark.trio
async def test_navigate(aqtbot, running_backend, logged_gui_with_workspace, monkeypatch):
    w_f = logged_gui_with_workspace.test_get_files_widget()
    central_widget = logged_gui_with_workspace.test_get_central_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1
    assert central_widget.label_title2.text() == "Workspace"
    assert central_widget.label_title3.text() == "/"

    await create_directories(logged_gui_with_workspace, aqtbot, monkeypatch, ["Dir1", "Dir2"])

    assert w_f.table_files.rowCount() == 3
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace
        assert w_f.table_files.item(1, i).data(TYPE_DATA_INDEX) == FileType.Folder
        assert w_f.table_files.item(2, i).data(TYPE_DATA_INDEX) == FileType.Folder
    assert w_f.table_files.item(1, 1).text() == "Dir1"
    assert w_f.table_files.item(2, 1).text() == "Dir2"

    # Navigate to one directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "Dir1")
    assert w_f.table_files.rowCount() == 1
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentFolder
    assert central_widget.label_title2.text() == "Workspace"
    assert central_widget.label_title3.text() == "/Dir1"

    # Navigate to the workspace root
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.ParentFolder, "Parent Folder")
    assert w_f.table_files.rowCount() == 3
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace
        assert w_f.table_files.item(1, i).data(TYPE_DATA_INDEX) == FileType.Folder
        assert w_f.table_files.item(2, i).data(TYPE_DATA_INDEX) == FileType.Folder
    assert w_f.table_files.item(1, 1).text() == "Dir1"
    assert w_f.table_files.item(2, 1).text() == "Dir2"
    assert central_widget.label_title2.text() == "Workspace"
    assert central_widget.label_title3.text() == "/"

    # Navigate to workspaces list
    wk_w = logged_gui_with_workspace.test_get_workspaces_widget()

    w_f.table_files.item_activated.emit(FileType.ParentWorkspace, "Parent Workspace")

    def _workspace_widget_visible():
        assert wk_w.isVisible()
        assert not w_f.isVisible()

    await aqtbot.wait_until(_workspace_widget_visible)


@pytest.mark.skip("TMP_SKIP")
@pytest.mark.gui
@pytest.mark.trio
async def test_show_inconsistent_dir(
    aqtbot, running_backend, logged_gui_with_workspace, monkeypatch, alice_user_fs, alice2_user_fs
):
    central_widget = logged_gui_with_workspace.test_get_central_widget()

    alice2_workspace = await create_inconsistent_workspace(alice2_user_fs)
    await alice2_user_fs.sync()
    await alice_user_fs.sync()
    alice_workspace = alice_user_fs.get_workspace(alice2_workspace.workspace_id)
    await alice_workspace.sync()

    w_f = logged_gui_with_workspace.test_get_files_widget()
    wk_w = logged_gui_with_workspace.test_get_workspaces_widget()

    # Navigate to workspaces list
    async with aqtbot.wait_signal(wk_w.list_success):
        w_f.table_files.item_activated.emit(FileType.ParentWorkspace, "Parent Workspace")
    assert wk_w.isVisible() is True
    assert w_f.isVisible() is False

    def workspace_button_enabled():
        assert wk_w.layout_workspaces.count() == 2
        wk_button = wk_w.layout_workspaces.itemAt(1).widget()
        assert wk_button.switch_button.isChecked()

    await aqtbot.wait_until(workspace_button_enabled)

    wk_button = wk_w.layout_workspaces.itemAt(1).widget()
    assert wk_button.name == "w"

    async with aqtbot.wait_signal(wk_w.load_workspace_clicked):
        await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)

    assert wk_w.isVisible() is False
    assert w_f.isVisible() is True

    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass

    assert w_f.table_files.rowCount() == 2
    assert central_widget.label_title2.text() == "w"
    assert central_widget.label_title3.text() == "/"

    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "rep")

    assert w_f.table_files.rowCount() == 3
    assert central_widget.label_title2.text() == "w"
    assert central_widget.label_title3.text() == "/rep"
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentFolder
        assert w_f.table_files.item(1, i).data(TYPE_DATA_INDEX) == FileType.File
        assert w_f.table_files.item(2, i).data(TYPE_DATA_INDEX) == FileType.Inconsistency
    assert w_f.table_files.item(1, 1).text() == "foo.txt"
    assert w_f.table_files.item(2, 1).text() == "newfail.txt"


@pytest.mark.gui
@pytest.mark.trio
async def test_delete_dirs(aqtbot, running_backend, logged_gui_with_workspace, monkeypatch):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(
        logged_gui_with_workspace, aqtbot, monkeypatch, ["Dir1", "Dir2", "Dir3"]
    )

    assert w_f.table_files.rowCount() == 4

    await aqtbot.run(w_f.table_files.select_rows, [1])
    assert len(w_f.table_files.selected_files()) == 1
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.ask_question", lambda *args: _("ACTION_FILE_DELETE")
    )
    async with aqtbot.wait_signals([w_f.delete_success, w_f.folder_stat_success]):
        w_f.table_files.delete_clicked.emit()

    # Wait until the file widget is refreshed by the timer
    while w_f.update_timer.isActive():
        async with aqtbot.wait_signal(w_f.folder_stat_success):
            pass

    assert w_f.table_files.rowCount() == 3

    # Then delete two
    await aqtbot.run(w_f.table_files.select_rows, [1, 2])
    assert len(w_f.table_files.selected_files()) == 2
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.ask_question", lambda *args: _("ACTION_FILE_DELETE_MULTIPLE")
    )
    async with aqtbot.wait_signals([w_f.delete_success, w_f.folder_stat_success]):
        w_f.table_files.delete_clicked.emit()

    def _files_deleted():
        assert w_f.table_files.rowCount() == 1

    # Wait until the file widget is refreshed by the timer
    await aqtbot.wait_until(_files_deleted)

    assert w_f.table_files.rowCount() == 1
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace


@pytest.mark.gui
@pytest.mark.trio
async def test_rename_dirs(aqtbot, running_backend, logged_gui_with_workspace, monkeypatch):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(
        logged_gui_with_workspace, aqtbot, monkeypatch, ["Dir1", "Dir2", "Dir3"]
    )

    assert w_f.table_files.rowCount() == 4
    # Select Dir1
    await aqtbot.run(w_f.table_files.select_rows, [1])
    assert len(w_f.table_files.selected_files()) == 1
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.get_text_input", lambda *args, **kwargs: ("Abcd")
    )
    # Rename Dir1 to Abcd
    async with aqtbot.wait_signals([w_f.rename_success, w_f.folder_stat_success]):
        w_f.table_files.rename_clicked.emit()

    # Wait until the file widget is refreshed by the timer
    while w_f.update_timer.isActive():
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 4
    item = w_f.table_files.item(1, 1)
    assert item.data(NAME_DATA_INDEX) == "Abcd"
    assert item.text() == "Abcd"

    # Select Dir2 and Dir3
    await aqtbot.run(w_f.table_files.select_rows, [2, 3])
    assert len(w_f.table_files.selected_files()) == 2
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.get_text_input", lambda *args, **kwargs: ("NewName")
    )
    async with aqtbot.wait_signals([w_f.rename_success, w_f.folder_stat_success]):
        w_f.table_files.rename_clicked.emit()

    # Wait until the file widget is refreshed by the timer
    while w_f.update_timer.isActive():
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 4
    item = w_f.table_files.item(2, 1)
    assert item.data(NAME_DATA_INDEX) == "NewName_1"
    assert item.text() == "NewName_1"
    item = w_f.table_files.item(3, 1)
    assert item.data(NAME_DATA_INDEX) == "NewName_2"
    assert item.text() == "NewName_2"


@pytest.mark.gui
@pytest.mark.trio
async def test_rename_dir_already_exists(
    aqtbot, running_backend, logged_gui_with_workspace, monkeypatch, autoclose_dialog
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(logged_gui_with_workspace, aqtbot, monkeypatch, ["Dir1", "Dir2"])
    assert w_f.table_files.rowCount() == 3

    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "Dir2")

    await create_directories(logged_gui_with_workspace, aqtbot, monkeypatch, ["Dir21"])
    assert w_f.table_files.rowCount() == 2

    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.ParentFolder, "Parent Folder")

    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(1, 0, 1, 0), True
    )
    assert len(w_f.table_files.selected_files()) == 1
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.get_text_input", lambda *args, **kwargs: ("Dir2")
    )
    async with aqtbot.wait_signal(w_f.rename_error):
        w_f.table_files.rename_clicked.emit()
    assert w_f.table_files.item(1, 1).text() == "Dir1"
    assert w_f.table_files.rowCount() == 3
    assert len(autoclose_dialog.dialogs) == 1
    assert autoclose_dialog.dialogs[0][0] == "Error"
    assert autoclose_dialog.dialogs[0][1] == "The file could not be renamed."


@pytest.mark.gui
@pytest.mark.trio
async def test_import_files(
    aqtbot, running_backend, logged_gui_with_workspace, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getOpenFileNames",
        classmethod(
            lambda *args, **kwargs: ([temp_dir / "file01.txt", temp_dir / "file02.txt"], True)
        ),
    )

    async with aqtbot.wait_signals(
        [w_f.button_import_files.clicked, w_f.import_success], timeout=3000
    ):
        await aqtbot.mouse_click(w_f.button_import_files, QtCore.Qt.LeftButton)

    # Wait until the file widget is refreshed
    while w_f.table_files.rowCount() < 3:
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 3
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"


@pytest.mark.gui
@pytest.mark.trio
async def test_import_dir(
    aqtbot, running_backend, logged_gui_with_workspace, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getExistingDirectory",
        classmethod(lambda *args, **kwargs: temp_dir),
    )

    async with aqtbot.wait_signals(
        [w_f.button_import_folder.clicked, w_f.import_success, w_f.folder_stat_success],
        timeout=3000,
    ):
        await aqtbot.mouse_click(w_f.button_import_folder, QtCore.Qt.LeftButton)

    assert w_f.table_files.rowCount() == 2
    assert w_f.table_files.item(1, 1).text() == temp_dir.name


@pytest.mark.gui
@pytest.mark.trio
async def test_cut_files(
    aqtbot, running_backend, logged_gui_with_files, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_files.test_get_files_widget()

    def _files_refreshed(count):
        assert w_f.table_files.rowCount() == count

    assert w_f is not None

    assert w_f.table_files.rowCount() == 4

    await aqtbot.run(w_f.table_files.select_rows, [2, 3])

    async with aqtbot.wait_signal(w_f.table_files.cut_clicked):
        await aqtbot.key_click(w_f.table_files, "X", modifier=QtCore.Qt.ControlModifier)

    assert w_f.clipboard is not None
    assert len(w_f.clipboard.files) == 2

    # Moving to sub directory
    await aqtbot.run(w_f.table_files.item_activated.emit, FileType.Folder, "dir1")

    def _switched_folder():
        c_w = logged_gui_with_files.test_get_central_widget()
        assert c_w.label_title3.text() == "/dir1"

    await aqtbot.wait_until(_switched_folder)

    await aqtbot.wait_until(partial(_files_refreshed, 1))

    async with aqtbot.wait_signals([w_f.table_files.paste_clicked, w_f.move_success], timeout=2000):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    await aqtbot.wait_until(partial(_files_refreshed, 3), timeout=2000)

    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"


@pytest.mark.gui
@pytest.mark.trio
async def test_copy_files(
    aqtbot, running_backend, logged_gui_with_files, monkeypatch, autoclose_dialog, temp_dir
):
    # Wait until the file widget is refreshed
    def _files_refreshed(count):
        assert w_f.table_files.rowCount() == count

    w_f = logged_gui_with_files.test_get_files_widget()

    assert w_f is not None

    assert w_f.table_files.rowCount() == 4

    await aqtbot.run(w_f.table_files.select_rows, [2, 3])

    async with aqtbot.wait_signal(w_f.table_files.copy_clicked):
        await aqtbot.key_click(w_f.table_files, "C", modifier=QtCore.Qt.ControlModifier)

    assert w_f.clipboard is not None
    assert len(w_f.clipboard.files) == 2

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")

    def _switched_folder():
        c_w = logged_gui_with_files.test_get_central_widget()
        assert c_w.label_title3.text() == "/dir1"

    await aqtbot.wait_until(_switched_folder)

    assert w_f.table_files.rowCount() == 1

    async with aqtbot.wait_signals([w_f.table_files.paste_clicked, w_f.copy_success], timeout=2000):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    await aqtbot.wait_until(partial(_files_refreshed, 3), timeout=2000)

    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"

    # Moving back
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.ParentFolder, "Parent Folder")

    await aqtbot.wait_until(partial(_files_refreshed, 4))

    assert w_f.table_files.rowCount() == 4
    assert w_f.table_files.item(1, 1).text() == "dir1"
    assert w_f.table_files.item(2, 1).text() == "file01.txt"
    assert w_f.table_files.item(3, 1).text() == "file02.txt"


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(logged_gui_create_two_workspaces=True)
async def test_copy_cut_folders_and_files_between_two_workspaces(
    aqtbot, running_backend, monkeypatch, logged_gui_with_files, autoclose_dialog
):
    # Wait until the file widget is refreshed
    def _files_displayed(files_nb):
        assert w_f.table_files.rowCount() == files_nb

    def _workspace_widget_visible():
        assert wk_widget.isVisible()
        assert not w_f.isVisible()

    # Getting files widget to copy the 2 files
    logged_gui = logged_gui_with_files
    w_f = logged_gui.test_get_files_widget()
    mount_widget = logged_gui.test_get_mount_widget()

    # Getting workspace widget
    wk_widget = logged_gui.test_get_workspaces_widget()

    assert w_f is not None
    assert mount_widget is not None

    # 2 files displayed + 1 folder + parent button
    assert w_f.table_files.rowCount() == 4
    # Checking clipboard and global clipboard are both empty
    assert w_f.clipboard is None
    assert mount_widget.global_clipboard is None

    # Selecting the two files to copy
    await aqtbot.run(w_f.table_files.select_rows, [2, 3])

    # Copy the 2 files of first workspace
    async with aqtbot.wait_signal(w_f.table_files.copy_clicked):
        await aqtbot.key_click(w_f.table_files, "C", modifier=QtCore.Qt.ControlModifier)

    # Test local widget file clipboard
    assert w_f.clipboard is not None
    assert len(w_f.clipboard.files) == 2

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    # Should have only the parent directory displayed
    await aqtbot.wait_until(lambda: _files_displayed(1))

    # Paste the 2 files in subfolder
    async with aqtbot.wait_signals([w_f.table_files.paste_clicked, w_f.copy_success], timeout=2000):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    await aqtbot.wait_until(lambda: _files_displayed(3), timeout=3000)

    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"

    # Moving back to root
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.ParentFolder, "Parent Folder")

    await aqtbot.wait_until(lambda: _files_displayed(4))

    # Select cut range
    await aqtbot.run(w_f.table_files.select_rows, [1, 2, 3])

    # Cut the 2 files and the folder of first workspace
    async with aqtbot.wait_signal(w_f.table_files.cut_clicked):
        await aqtbot.key_click(w_f.table_files, "X", modifier=QtCore.Qt.ControlModifier)

    # Check both clipboards is not none
    assert w_f.clipboard is not None
    assert len(w_f.clipboard.files) == 3
    assert mount_widget.global_clipboard is not None

    # Go to workspace list to paste it in second workspace
    w_f.table_files.item_activated.emit(FileType.ParentWorkspace, "Parent Workspace")

    await aqtbot.wait_until(_workspace_widget_visible)
    # Selecting the second workspace
    wk_button = wk_widget.layout_workspaces.itemAt(1).widget()
    assert wk_button.name == "Workspace2"

    # Going to second workspace
    async with aqtbot.wait_signal(wk_widget.load_workspace_clicked):
        await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)
    await aqtbot.wait_until(lambda: _files_displayed(1))

    # Paste the files/folders of first workspace in second workspace folder
    async with aqtbot.wait_signals([w_f.table_files.paste_clicked, w_f.move_success], timeout=2000):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    await aqtbot.wait_until(lambda: _files_displayed(4))

    # Check clipboards, should be None because we used cut
    assert w_f.clipboard is None
    assert mount_widget.global_clipboard is None

    # Check files/folder in root directory
    assert w_f.table_files.item(1, 1).text() == "dir1"
    assert w_f.table_files.item(2, 1).text() == "file01.txt"
    assert w_f.table_files.item(3, 1).text() == "file02.txt"

    # Prepare copy in second workspace, select cut range
    await aqtbot.run(w_f.table_files.select_rows, [1, 2, 3])

    # Copy files and folder of second workspace
    async with aqtbot.wait_signal(w_f.table_files.copy_clicked):
        await aqtbot.key_click(w_f.table_files, "C", modifier=QtCore.Qt.ControlModifier)

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    await aqtbot.wait_until(lambda: _files_displayed(3))

    # Check if files in subdirectory have been pasted correctly
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"

    # Going back to first workspace to check deletion because of cut
    w_f.table_files.item_activated.emit(FileType.ParentWorkspace, "Parent Workspace")

    await aqtbot.wait_until(_workspace_widget_visible)
    wk_button = wk_widget.layout_workspaces.itemAt(0).widget()
    assert wk_button.name == "Workspace"

    async with aqtbot.wait_signal(wk_widget.load_workspace_clicked):
        await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)
    await aqtbot.wait_until(lambda: _files_displayed(1))

    # Paste the files/folders of second workspace in first workspace folder
    async with aqtbot.wait_signal(w_f.table_files.paste_clicked):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    await aqtbot.wait_until(lambda: _files_displayed(4))

    # Check files/folder in root directory
    assert w_f.table_files.item(1, 1).text() == "dir1"
    assert w_f.table_files.item(2, 1).text() == "file01.txt"
    assert w_f.table_files.item(3, 1).text() == "file02.txt"

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    await aqtbot.wait_until(lambda: _files_displayed(3))
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"

    # Moving one last time to 2nd workspace to check files are still there
    w_f.table_files.item_activated.emit(FileType.ParentWorkspace, "Parent Workspace")

    await aqtbot.wait_until(_workspace_widget_visible)
    wk_button = wk_widget.layout_workspaces.itemAt(1).widget()
    assert wk_button.name == "Workspace2"

    async with aqtbot.wait_signal(wk_widget.load_workspace_clicked):
        await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)
    await aqtbot.wait_until(lambda: _files_displayed(4))

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    await aqtbot.wait_until(lambda: _files_displayed(3))
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"

    # Check clipboards, should exist because we used copy
    assert w_f.clipboard is not None
    assert mount_widget.global_clipboard is not None

    # Test copy again in subdirectory
    async with aqtbot.wait_signal(w_f.table_files.paste_clicked):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    await aqtbot.wait_until(lambda: _files_displayed(6))
    assert w_f.table_files.item(1, 1).text() == "dir1"
    assert w_f.table_files.item(2, 1).text() == "file01.txt"
    assert w_f.table_files.item(3, 1).text() == "file02.txt"
    assert w_f.table_files.item(4, 1).text() == "file01 (2).txt"
    assert w_f.table_files.item(5, 1).text() == "file02 (2).txt"

    # Moving to sub/sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    await aqtbot.wait_until(lambda: _files_displayed(3))
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"

    # Check clipboards, should exist because we used copy
    assert w_f.clipboard is not None
    assert mount_widget.global_clipboard is not None


@pytest.mark.gui
@pytest.mark.trio
async def test_copy_files_same_name(
    aqtbot, running_backend, logged_gui_with_files, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_files.test_get_files_widget()

    assert w_f is not None

    assert w_f.table_files.rowCount() == 4

    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(2, 0, 3, 0), True
    )

    async with aqtbot.wait_signal(w_f.table_files.copy_clicked):
        await aqtbot.key_click(w_f.table_files, "C", modifier=QtCore.Qt.ControlModifier)

    assert w_f.clipboard is not None

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    assert w_f.table_files.rowCount() == 1

    async with aqtbot.wait_signal(w_f.table_files.paste_clicked):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    def _files_refreshed(count):
        assert w_f.table_files.rowCount() == count

    await aqtbot.wait_until(partial(_files_refreshed, 3))

    async with aqtbot.wait_signal(w_f.table_files.paste_clicked):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    await aqtbot.wait_until(partial(_files_refreshed, 5))

    assert w_f.table_files.rowCount() == 5
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"
    assert w_f.table_files.item(3, 1).text() == "file01 (2).txt"
    assert w_f.table_files.item(4, 1).text() == "file02 (2).txt"


@pytest.mark.gui
@pytest.mark.trio
async def test_cut_dir_in_itself(
    aqtbot, running_backend, logged_gui_with_files, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_files.test_get_files_widget()

    assert w_f is not None

    assert w_f.table_files.rowCount() == 4

    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(1, 0, 1, 0), True
    )

    async with aqtbot.wait_signal(w_f.table_files.cut_clicked):
        await aqtbot.key_click(w_f.table_files, "X", modifier=QtCore.Qt.ControlModifier)

    assert w_f.clipboard is not None

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    assert w_f.table_files.rowCount() == 1

    async with aqtbot.wait_signal(w_f.table_files.paste_clicked):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    # Moving back
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.ParentFolder, "Parent Folder")

    # Wait until the file widget is refreshed
    while w_f.table_files.rowCount() < 4:
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass
    assert w_f.table_files.rowCount() == 4

    assert w_f.table_files.item(1, 1).text() == "dir1"
    assert w_f.table_files.item(2, 1).text() == "file01.txt"
    assert w_f.table_files.item(3, 1).text() == "file02.txt"

    # Moving to sub directory
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "dir1")
    assert w_f.table_files.rowCount() == 1


@pytest.mark.gui
@pytest.mark.trio
async def test_drag_and_drop(
    aqtbot,
    running_backend,
    logged_gui_with_workspace,
    monkeypatch,
    autoclose_dialog,
    temp_dir,
    qt_thread_gateway,
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None

    def _file_widget_loaded():
        assert w_f.table_files.rowCount() == 1

    await aqtbot.wait_until(_file_widget_loaded)

    assert w_f.label_role.text() == _("TEXT_WORKSPACE_ROLE_OWNER")

    def _import_file():
        mime_data = QtCore.QMimeData()
        mime_data.setUrls([QtCore.QUrl.fromLocalFile(str(temp_dir / "file01.txt"))])
        drop_event = QtGui.QDropEvent(
            w_f.table_files.pos(),
            QtCore.Qt.MoveAction,
            mime_data,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
        )
        w_f.table_files.dropEvent(drop_event)

    await qt_thread_gateway.send_action(_import_file)

    def _file_imported():
        assert w_f.table_files.rowCount() == 2
        assert w_f.table_files.item(1, 1).text() == "file01.txt"

    await aqtbot.wait_until(_file_imported)


@pytest.mark.gui
@pytest.mark.trio
async def test_drag_and_drop_read_only(
    aqtbot,
    running_backend,
    logged_gui_with_workspace,
    monkeypatch,
    autoclose_dialog,
    temp_dir,
    qt_thread_gateway,
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None

    def _file_widget_loaded():
        assert w_f.table_files.rowCount() == 1

    await aqtbot.wait_until(_file_widget_loaded)

    w_f.table_files.current_user_role = WorkspaceRole.READER

    def _import_file():
        mime_data = QtCore.QMimeData()
        mime_data.setUrls([QtCore.QUrl.fromLocalFile(str(temp_dir / "file01.txt"))])
        drop_event = QtGui.QDropEvent(
            w_f.table_files.pos(),
            QtCore.Qt.MoveAction,
            mime_data,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
        )
        w_f.table_files.dropEvent(drop_event)

    await qt_thread_gateway.send_action(_import_file)

    def _import_failed():
        assert autoclose_dialog.dialogs == [("Error", _("TEXT_FILE_DROP_WORKSPACE_IS_READ_ONLY"))]
        assert w_f.table_files.rowCount() == 1

    await aqtbot.wait_until(_import_failed)


# Cannot chmod on Windows
@pytest.mark.linux
@pytest.mark.gui
@pytest.mark.trio
async def test_import_one_file_permission_denied(
    aqtbot, running_backend, logged_gui_with_workspace, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    # Changing file permissions
    os.chmod(temp_dir / "file01.txt", 000)
    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getOpenFileNames",
        classmethod(lambda *args, **kwargs: ([temp_dir / "file01.txt"], True)),
    )

    async with aqtbot.wait_signal(w_f.button_import_files.clicked):
        await aqtbot.mouse_click(w_f.button_import_files, QtCore.Qt.LeftButton)

    def _import_failed():
        assert autoclose_dialog.dialogs == [("Error", _("TEXT_FILE_IMPORT_ONE_PERMISSION_ERROR"))]
        assert w_f.table_files.rowCount() == 1

    await aqtbot.wait_until(_import_failed, timeout=3000)


# Cannot chmod on Windows
@pytest.mark.linux
@pytest.mark.gui
@pytest.mark.trio
async def test_import_multiple_files_error(
    aqtbot, running_backend, logged_gui_with_workspace, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_workspace.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    # Changing file permissions
    os.chmod(temp_dir / "file01.txt", 000)

    monkeypatch.setattr(
        "PyQt5.QtWidgets.QFileDialog.getOpenFileNames",
        classmethod(
            lambda *args, **kwargs: ([temp_dir / "file01.txt", temp_dir / "file02.txt"], True)
        ),
    )

    async with aqtbot.wait_signal(w_f.button_import_files.clicked):
        await aqtbot.mouse_click(w_f.button_import_files, QtCore.Qt.LeftButton)

    def _import_error_shown():
        assert autoclose_dialog.dialogs == [
            ("Error", _("TEXT_FILE_IMPORT_MULTIPLE_PERMISSION_ERROR"))
        ]
        assert w_f.table_files.rowCount() == 2

    await aqtbot.wait_until(_import_error_shown, timeout=3000)

    assert w_f.table_files.item(1, 1).text() == "file02.txt"


@pytest.mark.gui
@pytest.mark.trio
async def test_open_file_failed(
    aqtbot, running_backend, logged_gui_with_files, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui_with_files.test_get_files_widget()

    assert w_f is not None

    assert w_f.table_files.rowCount() == 4

    monkeypatch.setattr(
        "parsec.core.gui.files_widget.desktop.open_file", lambda *args, **kwargs: (False)
    )
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.ask_question",
        lambda *args, **kwargs: (_("ACTION_FILE_OPEN_MULTIPLE")),
    )

    # Open the file selected
    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(2, 0, 2, 0), True
    )
    assert len(w_f.table_files.selected_files()) == 1
    w_f.table_files.open_clicked.emit()

    def _open_single_file_error_shown():
        assert autoclose_dialog.dialogs == [
            ("Error", _("TEXT_FILE_OPEN_ERROR_file").format(file="file01.txt"))
        ]

    await aqtbot.wait_until(_open_single_file_error_shown)

    autoclose_dialog.reset()

    # Open a file by double click
    w_f.table_files.item_activated.emit(FileType.File, "file01.txt")

    await aqtbot.wait_until(_open_single_file_error_shown)

    autoclose_dialog.reset()

    # Open multiple files
    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(2, 0, 3, 0), True
    )
    assert len(w_f.table_files.selected_files()) == 2
    w_f.table_files.open_clicked.emit()

    def _open_multiple_files_error_shown():
        assert autoclose_dialog.dialogs == [("Error", _("TEXT_FILE_OPEN_MULTIPLE_ERROR"))]

    await aqtbot.wait_until(_open_multiple_files_error_shown)
