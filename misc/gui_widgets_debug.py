#!/usr/bin/env python
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLineEdit,
    QListView,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QWidget,
)

from parsec.core.gui import custom_dialogs
from parsec.core.gui.resources_rc import *  # noqa

SMALL_TEXT = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
LARGE_TEXT = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit.<br />"
    "Cras aliquet convallis lectus, id euismod turpis. Nunc pulvinar bibendum ante nec ornare.<br />"
    "Mauris vulputate, neque eget pellentesque tristique, ipsum nulla ultricies ex, in vestibulum<br /> "
    "ex diam sed ligula. Donec nec elit turpis. Vivamus venenatis sem nibh.<br />"
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Praesent vulputate quis mi id "
    "dictum. Sed at lorem quis arcu rutrum maximus. Nulla molestie, ipsum non laoreet cursus, "
    "justo massa egestas dolor, in faucibus nulla nunc at augue. Etiam vel consectetur odio.<br />"
    "Nam libero nisl, tempor quis lorem vel, mattis venenatis tellus.<br />"
    "Aenean fringilla tincidunt urna quis tincidunt. Vivamus at suscipit ante. "
    "Vivamus nisl metus, ornare nec efficitur ac, posuere in lacus."
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setObjectName("MainWindow")

        layout = QGridLayout()
        w = QWidget()
        self.setCentralWidget(w)
        w.setLayout(layout)

        ws = []

        b = QPushButton("Open Question Small")
        b.clicked.connect(self.open_question_small)
        ws.append(b)

        b = QPushButton("Open Question Large")
        b.clicked.connect(self.open_question_large)
        ws.append(b)

        b = QPushButton("Open Error Small")
        b.clicked.connect(self.open_error_small)
        ws.append(b)

        b = QPushButton("Open Error Large")
        b.clicked.connect(self.open_error_large)
        ws.append(b)

        b = QPushButton("Open Info Small")
        b.clicked.connect(self.open_info_small)
        ws.append(b)

        b = QPushButton("Open Info Large")
        b.clicked.connect(self.open_info_large)
        ws.append(b)

        b = QPushButton("Open Get Text Small")
        b.clicked.connect(self.open_get_text_small)
        ws.append(b)

        b = QPushButton("Open Get Text Large")
        b.clicked.connect(self.open_get_text_large)
        ws.append(b)

        ws.append(QPushButton("Enabled"))
        b = QPushButton("Disabled")
        b.setDisabled(True)
        ws.append(b)
        le = QLineEdit()
        le.setText("Normal")
        ws.append(le)
        le = QLineEdit()
        le.setReadOnly(True)
        le.setText("Read Only")
        ws.append(le)
        le = QLineEdit()
        le.setDisabled(True)
        le.setText("Disabled")
        ws.append(le)
        ws.append(QSpinBox())
        sp = QSpinBox()
        sp.setDisabled(True)
        ws.append(sp)
        ws.append(QTimeEdit())
        te = QTimeEdit()
        te.setDisabled(True)
        ws.append(te)
        cb = QComboBox()
        cb.addItem("Item 1")
        cb.addItem("Item 2")
        cb.setView(QListView())
        ws.append(cb)
        cb = QComboBox()
        cb.addItem("Item 1")
        cb.addItem("Item 2")
        cb.setDisabled(True)
        ws.append(cb)
        ws.append(QCheckBox("Normal"))
        cb = QCheckBox("Disabled")
        cb.setDisabled(True)
        ws.append(cb)

        with open("parsec/core/gui/rc/styles/main.css") as fd:
            self.setStyleSheet(fd.read())
            self.ensurePolished()

        col = 0
        for idx, w in enumerate(ws):
            if idx % 3 == 0 and idx > 0:
                col = 0
            w.setToolTip("ToolTip example")
            layout.addWidget(w, int(idx / 3), col)
            col += 1

    def open_question_small(self):
        custom_dialogs.ask_question(
            self,
            title=SMALL_TEXT,
            message=SMALL_TEXT,
            button_texts=["BUTTON1", "BUTTON2", "BUTTON3"],
        )

    def open_question_large(self):
        custom_dialogs.ask_question(
            self,
            title=SMALL_TEXT,
            message=LARGE_TEXT,
            button_texts=["BUTTON1", "BUTTON2", "BUTTON3"],
        )

    def open_error_small(self):
        exc = None
        try:
            1337 / 0
        except ZeroDivisionError as e:
            exc = e

        custom_dialogs.show_error(self, message=SMALL_TEXT, exception=exc)

    def open_error_large(self):
        exc = None
        try:
            1337 / 0
        except ZeroDivisionError as e:
            exc = e

        custom_dialogs.show_error(self, message=LARGE_TEXT, exception=exc)

    def open_info_small(self):
        custom_dialogs.show_info(self, message=SMALL_TEXT, button_text="Continue")

    def open_info_large(self):
        custom_dialogs.show_info(self, message=LARGE_TEXT, button_text="Continue")

    def open_get_text_small(self):
        custom_dialogs.get_text_input(
            self,
            title=SMALL_TEXT,
            message=SMALL_TEXT,
            placeholder="Placeholder",
            default_text="Default text",
            completion=["Abcd", "Bcde", "Cdef", "Defg", "Efgh", "Fghi", "Ghij", "Hijk", "Ijkl"],
            button_text="Go",
        )

    def open_get_text_large(self):
        custom_dialogs.get_text_input(
            self,
            title=SMALL_TEXT,
            message=LARGE_TEXT,
            placeholder="Placeholder",
            default_text="Default text",
            completion=["Abcd", "Bcde", "Cdef", "Defg", "Efgh", "Fghi", "Ghij", "Hijk", "Ijkl"],
            button_text="Go",
        )


if __name__ == "__main__":
    app = QApplication([])
    win = MainWindow()

    win.showMaximized()

    app.exec_()
