# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import uuid

import pendulum
import pytest
from PyQt5 import QtCore

from parsec.core.gui.file_table import FileTable
from parsec.core.gui.lang import switch_language


@pytest.mark.gui
def test_file_table_parent_folder(qtbot, core_config):
    switch_language(core_config, "en")

    w = FileTable(parent=None)
    qtbot.addWidget(w)

    assert w.rowCount() == 0
    assert w.columnCount() == 5

    w.add_parent_folder()
    assert w.rowCount() == 1
    assert w.item(0, 1).text() == "Parent folder"


@pytest.mark.gui
def test_file_table_parent_workspace(qtbot, core_config):
    switch_language(core_config, "en")

    w = FileTable(parent=None)
    qtbot.addWidget(w)

    assert w.rowCount() == 0
    assert w.columnCount() == 5

    w.add_parent_workspace()
    assert w.rowCount() == 1
    assert w.item(0, 1).text() == "Workspaces list"


@pytest.mark.gui
def test_file_table_clear(qtbot):

    w = FileTable(parent=None)
    qtbot.addWidget(w)

    w.add_parent_workspace()
    assert w.rowCount() == 1
    w.clear()
    assert w.rowCount() == 0


@pytest.mark.gui
def test_file_table_sort(qtbot, core_config):
    switch_language(core_config, "en")

    w = FileTable(parent=None)
    qtbot.addWidget(w)
    w.add_parent_workspace()
    w.add_folder("Dir1", uuid.uuid4(), True)
    w.add_file(
        "File1.txt",
        uuid.uuid4(),
        100,
        pendulum.datetime(2000, 1, 15),
        pendulum.datetime(2000, 1, 20),
        True,
    )
    w.add_file(
        "AnotherFile.txt",
        uuid.uuid4(),
        80,
        pendulum.datetime(2000, 1, 10),
        pendulum.datetime(2000, 1, 25),
        True,
    )
    assert w.rowCount() == 4
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "Dir1"
    assert w.item(2, 1).text() == "File1.txt"
    assert w.item(3, 1).text() == "AnotherFile.txt"

    # Name
    w.sortByColumn(1, QtCore.Qt.AscendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "AnotherFile.txt"
    assert w.item(2, 1).text() == "Dir1"
    assert w.item(3, 1).text() == "File1.txt"

    w.sortByColumn(1, QtCore.Qt.DescendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "File1.txt"
    assert w.item(2, 1).text() == "Dir1"
    assert w.item(3, 1).text() == "AnotherFile.txt"

    # Created
    w.sortByColumn(2, QtCore.Qt.AscendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "Dir1"
    assert w.item(2, 1).text() == "AnotherFile.txt"
    assert w.item(3, 1).text() == "File1.txt"

    w.sortByColumn(2, QtCore.Qt.DescendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "File1.txt"
    assert w.item(2, 1).text() == "AnotherFile.txt"
    assert w.item(3, 1).text() == "Dir1"

    # Updated
    w.sortByColumn(3, QtCore.Qt.AscendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "Dir1"
    assert w.item(2, 1).text() == "File1.txt"
    assert w.item(3, 1).text() == "AnotherFile.txt"

    w.sortByColumn(3, QtCore.Qt.DescendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "AnotherFile.txt"
    assert w.item(2, 1).text() == "File1.txt"
    assert w.item(3, 1).text() == "Dir1"

    # Size
    w.sortByColumn(4, QtCore.Qt.AscendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "Dir1"
    assert w.item(2, 1).text() == "AnotherFile.txt"
    assert w.item(3, 1).text() == "File1.txt"

    w.sortByColumn(4, QtCore.Qt.DescendingOrder)
    assert w.item(0, 1).text() == "Workspaces list"
    assert w.item(1, 1).text() == "File1.txt"
    assert w.item(2, 1).text() == "AnotherFile.txt"
    assert w.item(3, 1).text() == "Dir1"
