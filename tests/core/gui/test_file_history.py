# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pytest
from PyQt5 import QtWidgets


@pytest.fixture
def catch_file_history_widget(widget_catcher_factory):
    return widget_catcher_factory("parsec.core.gui.file_history_widget.FileHistoryWidget")


@pytest.mark.gui
@pytest.mark.trio
async def test_file_history(
    aqtbot, running_backend, logged_gui, autoclose_dialog, catch_file_history_widget
):
    core = logged_gui.test_get_core()
    wid = await core.user_fs.workspace_create("wksp1")
    wfs = core.user_fs.get_workspace(wid)

    w_w = logged_gui.test_get_workspaces_widget()

    def _workspace_available():
        assert w_w.layout_workspaces.count() == 1

    await aqtbot.wait_until(_workspace_available)

    f_w = await logged_gui.test_switch_to_files_widget("wksp1")

    # Add an entry to the workspace
    await wfs.touch("/file.txt")
    await wfs.sync()
    await wfs.write_bytes("/file.txt", data=b"v2")
    await wfs.sync()

    def _entry_available():
        assert f_w.table_files.rowCount() == 2

    await aqtbot.wait_until(_entry_available)

    # First select the entry...
    await aqtbot.run(
        f_w.table_files.setRangeSelected, QtWidgets.QTableWidgetSelectionRange(1, 0, 1, 0), True
    )

    def _entry_selected():
        assert f_w.table_files.selectedItems()

    await aqtbot.wait_until(_entry_selected)

    # ...then ask for history
    await aqtbot.run(f_w.table_files.show_history_clicked.emit)

    hf_w = await catch_file_history_widget()

    def _history_displayed():
        assert hf_w.isVisible()
        assert hf_w.layout_history.count() == 1
        hb2_w = hf_w.layout_history.itemAt(0).widget()
        assert hb2_w.label_user.text() == "Boby McBobFace"
        assert hb2_w.label_version.text() == "1"

    await aqtbot.wait_until(_history_displayed)
