# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from PyQt5 import QtCore, QtWidgets

from parsec.core.gui import custom_dialogs


@pytest.mark.gui
def test_get_text_dialog_close(qtbot):
    w = custom_dialogs.TextInputWidget(message="Message")
    d = custom_dialogs.GreyedDialog(w, title="Title", parent=None)
    qtbot.addWidget(d)
    d.show()

    assert d.isVisible() is True
    assert w.isVisible() is True
    assert d.label_title.text() == "Title"
    assert w.label_message.text() == "Message"
    assert w.line_edit_text.placeholderText() == ""
    assert w.line_edit_text.text() == ""
    qtbot.mouseClick(d.button_close, QtCore.Qt.LeftButton)
    assert d.result() == QtWidgets.QDialog.Rejected
    assert w.text == ""
    assert w.isVisible() is False


@pytest.mark.gui
def test_get_text_dialog_accept(qtbot):
    w = custom_dialogs.TextInputWidget(
        message="Message", placeholder="Placeholder", default_text="Default"
    )
    d = custom_dialogs.GreyedDialog(w, title="Title", parent=None)

    d.closing.connect(w.on_closing)
    w.accepted.connect(d.accept)

    def _on_finished(return_code, text):
        assert return_code == QtWidgets.QDialog.Accepted
        assert text == "test"
        assert w.isVisible() is False

    w.finished.connect(_on_finished)

    qtbot.addWidget(d)
    d.show()

    assert d.isVisible() is True
    assert w.isVisible() is True
    assert d.label_title.text() == "Title"
    assert w.label_message.text() == "Message"
    assert w.line_edit_text.placeholderText() == "Placeholder"
    assert w.line_edit_text.text() == "Default"
    w.line_edit_text.setText("")
    qtbot.keyClicks(w.line_edit_text, "test")
    qtbot.mouseClick(w.button_ok, QtCore.Qt.LeftButton)


@pytest.mark.gui
def test_ask_question_no(qtbot):
    w = custom_dialogs.QuestionWidget(message="Message", button_texts=["YES", "NO"])
    d = custom_dialogs.GreyedDialog(w, title="Title", parent=None)

    d.closing.connect(w.on_closing)
    w.accepted.connect(d.accept)

    def _on_finished(return_code, answer):
        assert return_code == QtWidgets.QDialog.Accepted
        assert answer == "NO"
        assert w.isVisible() is False

    w.finished.connect(_on_finished)
    qtbot.addWidget(d)

    d.show()
    assert d.isVisible() is True
    assert w.isVisible() is True
    assert d.label_title.text() == "Title"
    assert w.label_message.text() == "Message"
    button_no = w.layout_buttons.itemAt(1).widget()
    assert button_no.text() == "NO"
    qtbot.mouseClick(button_no, QtCore.Qt.LeftButton)


@pytest.mark.gui
def test_ask_question_yes(qtbot):
    w = custom_dialogs.QuestionWidget(message="Message", button_texts=["YES", "NO"])
    d = custom_dialogs.GreyedDialog(w, title="Title", parent=None)

    d.closing.connect(w.on_closing)
    w.accepted.connect(d.accept)

    def _on_finished(return_code, answer):
        assert return_code == QtWidgets.QDialog.Accepted
        assert answer == "YES"
        assert w.isVisible() is False

    w.finished.connect(_on_finished)

    d.show()
    assert d.isVisible() is True
    assert w.isVisible() is True
    assert d.label_title.text() == "Title"
    assert w.label_message.text() == "Message"
    button_yes = w.layout_buttons.itemAt(2).widget()
    assert button_yes.text() == "YES"
    qtbot.mouseClick(button_yes, QtCore.Qt.LeftButton)


@pytest.mark.gui
def test_ask_question_close(qtbot):
    w = custom_dialogs.QuestionWidget(message="Message", button_texts=["YES", "NO"])
    d = custom_dialogs.GreyedDialog(w, title="Title", parent=None)

    d.closing.connect(w.on_closing)
    w.accepted.connect(d.accept)

    def _on_finished(return_code, answer):
        assert return_code == QtWidgets.QDialog.Rejected
        assert answer == ""
        assert w.isVisible() is False

    w.finished.connect(_on_finished)
    qtbot.addWidget(d)

    d.show()
    assert d.isVisible() is True
    assert w.isVisible() is True
    assert d.label_title.text() == "Title"
    assert w.label_message.text() == "Message"
    qtbot.mouseClick(d.button_close, QtCore.Qt.LeftButton)
