# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pathlib

import pytest
from PyQt5 import QtCore, QtWidgets

from parsec.core.gui.file_items import NAME_DATA_INDEX, TYPE_DATA_INDEX, FileType
from parsec.core.gui.lang import translate as _
from parsec.core.local_device import save_device_with_password
from parsec.test_utils import create_inconsistent_workspace


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
async def logged_gui(
    aqtbot, gui_factory, autoclose_dialog, core_config, alice, running_backend, monkeypatch
):
    save_device_with_password(core_config.config_dir, alice, "P@ssw0rd")
    monkeypatch.setattr(
        "parsec.core.gui.workspaces_widget.WorkspacesWidget.RESET_TIMER_THRESHOLD", 0
    )

    gui = await gui_factory()
    lw = gui.test_get_login_widget()
    tabw = gui.test_get_tab()

    await aqtbot.key_clicks(lw.line_edit_password, "P@ssw0rd")

    async with aqtbot.wait_signals([lw.login_with_password_clicked, tabw.logged_in]):
        await aqtbot.mouse_click(lw.button_login, QtCore.Qt.LeftButton)

    central_widget = gui.test_get_central_widget()
    assert central_widget is not None

    wk_widget = gui.test_get_workspaces_widget()
    async with aqtbot.wait_signal(wk_widget.list_success):
        pass

    add_button = wk_widget.button_add_workspace
    assert add_button is not None

    monkeypatch.setattr(
        "parsec.core.gui.workspaces_widget.get_text_input", lambda *args, **kwargs: ("Workspace")
    )

    async with aqtbot.wait_signals(
        [wk_widget.create_success, wk_widget.list_success, wk_widget.mountpoint_started],
        timeout=2000,
    ):
        await aqtbot.mouse_click(add_button, QtCore.Qt.LeftButton)

    def workspace_button_ready():
        assert wk_widget.layout_workspaces.count() == 1
        wk_button = wk_widget.layout_workspaces.itemAt(0).widget()
        assert not isinstance(wk_button, QtWidgets.QLabel)

    await aqtbot.wait_until(workspace_button_ready)
    wk_button = wk_widget.layout_workspaces.itemAt(0).widget()
    assert wk_button.name == "Workspace"

    async with aqtbot.wait_signal(wk_widget.load_workspace_clicked):
        await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)

    yield gui


@pytest.fixture
async def logged_gui_with_files(aqtbot, logged_gui, running_backend, monkeypatch, temp_dir):
    w_f = logged_gui.test_get_files_widget()

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

    yield logged_gui


async def create_directories(logged_gui, aqtbot, monkeypatch, dir_names):
    central_widget = logged_gui.test_get_central_widget()
    assert central_widget is not None

    w_f = logged_gui.test_get_files_widget()
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
async def test_list_files(aqtbot, running_backend, logged_gui):
    w_f = logged_gui.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass

    central_widget = logged_gui.test_get_central_widget()
    assert central_widget.label_title2.text() == "Workspace"
    assert central_widget.label_title3.text() == "/"

    assert w_f.table_files.rowCount() == 1
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace


@pytest.mark.gui
@pytest.mark.trio
async def test_create_dir(aqtbot, running_backend, logged_gui, monkeypatch):
    w_f = logged_gui.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(logged_gui, aqtbot, monkeypatch, ["Dir1"])

    assert w_f.table_files.rowCount() == 2
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace
        assert w_f.table_files.item(1, i).data(TYPE_DATA_INDEX) == FileType.Folder
    assert w_f.table_files.item(1, 1).text() == "Dir1"


@pytest.mark.gui
@pytest.mark.trio
async def test_create_dir_already_exists(
    aqtbot, running_backend, logged_gui, monkeypatch, autoclose_dialog
):
    w_f = logged_gui.test_get_files_widget()

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
async def test_navigate(aqtbot, running_backend, logged_gui, monkeypatch):
    w_f = logged_gui.test_get_files_widget()
    central_widget = logged_gui.test_get_central_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1
    assert central_widget.label_title2.text() == "Workspace"
    assert central_widget.label_title3.text() == "/"

    await create_directories(logged_gui, aqtbot, monkeypatch, ["Dir1", "Dir2"])

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
    wk_w = logged_gui.test_get_workspaces_widget()
    async with aqtbot.wait_signal(wk_w.list_success):
        w_f.table_files.item_activated.emit(FileType.ParentWorkspace, "Parent Workspace")
    assert wk_w.isVisible() is True
    assert w_f.isVisible() is False


@pytest.mark.gui
@pytest.mark.trio
async def test_show_inconsistent_dir(
    aqtbot, running_backend, logged_gui, monkeypatch, alice_user_fs, alice2_user_fs
):
    central_widget = logged_gui.test_get_central_widget()

    alice2_workspace = await create_inconsistent_workspace(alice2_user_fs)
    await alice2_user_fs.sync()
    await alice_user_fs.sync()
    alice_workspace = alice_user_fs.get_workspace(alice2_workspace.workspace_id)
    await alice_workspace.sync()

    w_f = logged_gui.test_get_files_widget()
    # Navigate to workspaces list
    wk_w = logged_gui.test_get_workspaces_widget()
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
async def test_delete_dirs(aqtbot, running_backend, logged_gui, monkeypatch):
    w_f = logged_gui.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(logged_gui, aqtbot, monkeypatch, ["Dir1", "Dir2", "Dir3"])

    assert w_f.table_files.rowCount() == 4

    # Delete one directory first
    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(1, 0, 1, 0), True
    )
    assert len(w_f.table_files.selected_files()) == 1
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.ask_question", lambda *args: _("ACTION_FILE_DELETE")
    )
    async with aqtbot.wait_signals([w_f.delete_success, w_f.folder_stat_success]):
        w_f.table_files.delete_clicked.emit()

    # Wait until the file widget is refreshed by the timer
    while w_f.update_timer.isActive():
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 3

    # Then delete two
    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(1, 0, 2, 0), True
    )
    assert len(w_f.table_files.selected_files()) == 2
    monkeypatch.setattr(
        "parsec.core.gui.files_widget.ask_question", lambda *args: _("ACTION_FILE_DELETE_MULTIPLE")
    )
    async with aqtbot.wait_signals([w_f.delete_success, w_f.folder_stat_success]):
        w_f.table_files.delete_clicked.emit()

    # Wait until the file widget is refreshed by the timer
    while w_f.update_timer.isActive():
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 1
    for i in range(5):
        assert w_f.table_files.item(0, i).data(TYPE_DATA_INDEX) == FileType.ParentWorkspace


@pytest.mark.gui
@pytest.mark.trio
async def test_rename_dirs(aqtbot, running_backend, logged_gui, monkeypatch):
    w_f = logged_gui.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(logged_gui, aqtbot, monkeypatch, ["Dir1", "Dir2", "Dir3"])

    assert w_f.table_files.rowCount() == 4
    # Select Dir1
    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(1, 0, 1, 0), True
    )
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
    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(2, 0, 3, 0), True
    )
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
    aqtbot, running_backend, logged_gui, monkeypatch, autoclose_dialog
):
    w_f = logged_gui.test_get_files_widget()

    assert w_f is not None
    async with aqtbot.wait_signal(w_f.folder_stat_success):
        pass
    assert w_f.table_files.rowCount() == 1

    await create_directories(logged_gui, aqtbot, monkeypatch, ["Dir1", "Dir2"])
    assert w_f.table_files.rowCount() == 3

    async with aqtbot.wait_signal(w_f.folder_stat_success):
        w_f.table_files.item_activated.emit(FileType.Folder, "Dir2")

    await create_directories(logged_gui, aqtbot, monkeypatch, ["Dir21"])
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
    aqtbot, running_backend, logged_gui, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui.test_get_files_widget()

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
    aqtbot, running_backend, logged_gui, monkeypatch, autoclose_dialog, temp_dir
):
    w_f = logged_gui.test_get_files_widget()

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

    assert w_f is not None

    assert w_f.table_files.rowCount() == 4

    await aqtbot.run(
        w_f.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(2, 0, 3, 0), True
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

    # Wait until the file widget is refreshed
    while w_f.table_files.rowCount() < 3:
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 3
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"


@pytest.mark.gui
@pytest.mark.trio
async def test_copy_files(
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

    # Wait until the file widget is refreshed
    while w_f.table_files.rowCount() < 3:
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass

    assert w_f.table_files.rowCount() == 3
    assert w_f.table_files.item(1, 1).text() == "file01.txt"
    assert w_f.table_files.item(2, 1).text() == "file02.txt"

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

    # Wait until the file widget is refreshed
    while w_f.table_files.rowCount() < 3:
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass
    assert w_f.table_files.rowCount() == 3

    async with aqtbot.wait_signal(w_f.table_files.paste_clicked):
        await aqtbot.key_click(w_f.table_files, "V", modifier=QtCore.Qt.ControlModifier)

    # Wait until the file widget is refreshed
    while w_f.table_files.rowCount() < 5:
        async with aqtbot.wait_signal(w_f.folder_stat_success, timeout=3000):
            pass
    assert w_f.table_files.rowCount() == 5

    assert w_f.table_files.rowCount() == 5
    assert w_f.table_files.item(1, 1).text() == "file01 (2).txt"
    assert w_f.table_files.item(2, 1).text() == "file01.txt"
    assert w_f.table_files.item(3, 1).text() == "file02 (2).txt"
    assert w_f.table_files.item(4, 1).text() == "file02.txt"


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
