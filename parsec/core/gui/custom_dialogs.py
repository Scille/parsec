# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import platform

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QApplication, QCompleter, QDialog, QStyle, QStyleOption, QWidget
from structlog import get_logger

from parsec.core.gui import desktop
from parsec.core.gui.custom_widgets import Button
from parsec.core.gui.lang import translate as _
from parsec.core.gui.parsec_application import ParsecApp
from parsec.core.gui.ui.error_widget import Ui_ErrorWidget
from parsec.core.gui.ui.greyed_dialog import Ui_GreyedDialog
from parsec.core.gui.ui.info_widget import Ui_InfoWidget
from parsec.core.gui.ui.input_widget import Ui_InputWidget
from parsec.core.gui.ui.question_widget import Ui_QuestionWidget

logger = get_logger()


class GreyedDialog(QDialog, Ui_GreyedDialog):
    def __init__(self, center_widget, title, parent, hide_close=False):
        super().__init__(None)
        self.setupUi(self)
        self.setModal(True)
        self.setObjectName("GreyedDialog")
        self.setWindowModality(Qt.ApplicationModal)
        self.button_close.apply_style()
        if platform.system() == "Windows":
            # SplashScreen on Windows freezes the Window
            self.setWindowFlags(Qt.FramelessWindowHint)
        else:
            # FramelessWindowHint on Linux (at least xfce) is less pretty
            self.setWindowFlags(Qt.SplashScreen)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.center_widget = center_widget
        self.main_layout.addWidget(center_widget)
        if not title and hide_close:
            self.widget_title.hide()
        if title:
            self.label_title.setText(title)
        if hide_close:
            self.button_close.hide()
        main_win = ParsecApp.get_main_window()
        if main_win:
            if main_win.isVisible():
                self.setParent(main_win)
                self.resize(main_win.size())
            else:
                self.showMaximized()
            self.move(0, 0)
        else:
            logger.error("GreyedDialog did not find the main window, this is probably a bug")
        self.setFocus()
        self.accepted.connect(self.on_finished)
        self.rejected.connect(self.on_finished)

    def paintEvent(self, event):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)

    def on_finished(self):
        if (
            self.result() == QDialog.Rejected
            and self.center_widget
            and getattr(self.center_widget, "on_close", None)
        ):
            getattr(self.center_widget, "on_close")()
        self.setParent(None)


class TextInputWidget(QWidget, Ui_InputWidget):
    def __init__(
        self,
        message,
        placeholder="",
        default_text="",
        completion=None,
        button_text=None,
        validator=None,
    ):
        super().__init__()
        self.setupUi(self)
        self.dialog = None
        button_text = button_text or _("ACTION_OK")
        self.button_ok.setText(button_text)
        self.label_message.setText(message)
        self.line_edit_text.setPlaceholderText(placeholder)
        self.line_edit_text.setText(default_text)
        if validator:
            self.line_edit_text.setValidator(validator)
        if completion:
            completer = QCompleter(completion)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.popup().setStyleSheet("border: 1px solid rgb(30, 78, 162);")
            self.line_edit_text.setCompleter(completer)
        self.button_ok.clicked.connect(self._on_button_clicked)
        self.setFocus()
        self.line_edit_text.setFocus()

    @property
    def text(self):
        return self.line_edit_text.text()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return:
            self._on_button_clicked()
        event.accept()

    def _on_button_clicked(self):
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when requesting user text input")


def get_text_input(
    parent,
    title,
    message,
    placeholder="",
    default_text="",
    completion=None,
    button_text=None,
    validator=None,
):
    w = TextInputWidget(
        message=message,
        placeholder=placeholder,
        default_text=default_text,
        completion=completion,
        button_text=button_text,
        validator=validator,
    )
    d = GreyedDialog(w, title=title, parent=parent)
    w.dialog = d
    w.line_edit_text.setFocus()
    result = d.exec_()
    if result == QDialog.Accepted:
        return w.text
    return None


class QuestionWidget(QWidget, Ui_QuestionWidget):
    def __init__(self, message, button_texts, radio_mode=False):
        super().__init__()
        self.setupUi(self)
        self.status = None
        self.dialog = None
        self.label_message.setText(message)
        for text in button_texts:
            b = Button(text)
            b.clicked_self.connect(self._on_button_clicked)
            b.setCursor(Qt.PointingHandCursor)
            if radio_mode:
                self.layout_radios.addWidget(b)
            else:
                self.layout_buttons.insertWidget(1, b)

    def _on_button_clicked(self, button):
        self.status = button.text()
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when asking question")


def ask_question(parent, title, message, button_texts, radio_mode=False):
    w = QuestionWidget(message=message, button_texts=button_texts, radio_mode=radio_mode)
    d = GreyedDialog(w, title=title, parent=parent)
    w.dialog = d
    status = d.exec_()
    if status == QDialog.Accepted:
        return w.status
    return None


class ErrorWidget(QWidget, Ui_ErrorWidget):
    def __init__(self, message, exception=None):
        super().__init__()
        self.setupUi(self)
        self.label_message.setText(message)
        self.label_icon.apply_style()
        self.text_details.hide()
        if not exception:
            self.button_details.hide()
        else:
            import traceback

            stack = traceback.format_tb(exception.__traceback__)
            if not stack:
                self.button_details.hide()
            else:
                except_text = "<b>{}</b><br /><br />{}".format(
                    str(exception).replace("\n", "<br />"), "<br />".join(stack)
                )
                except_text = except_text.replace("\n", "<br />")
                self.text_details.setHtml(except_text)
        self.button_details.clicked.connect(self.toggle_details)
        self.button_details.apply_style()
        self.button_copy.clicked.connect(self.copy_to_clipboard)
        self.button_copy.hide()
        self.button_copy.apply_style()

    def copy_to_clipboard(self):
        desktop.copy_to_clipboard(self.text_details.toPlainText())

    def toggle_details(self, checked):
        if not checked:
            self.text_details.hide()
            self.button_copy.hide()
        else:
            self.text_details.show()
            self.button_copy.show()


def show_error(parent, message, exception=None):
    w = ErrorWidget(message, exception)
    d = GreyedDialog(w, title=_("TEXT_ERR_DIALOG_TITLE"), parent=parent)
    return d.exec_()


class InfoWidget(QWidget, Ui_InfoWidget):
    def __init__(self, message, dialog=None, button_text=None):
        super().__init__()
        self.setupUi(self)
        self.dialog = dialog
        self.label_message.setText(message)
        self.label_icon.apply_style()
        self.button_ok.setText(_("ACTION_CONTINUE") or button_text)
        self.button_ok.clicked.connect(self._on_button_clicked)
        self.button_ok.setFocus()

    def _on_button_clicked(self, button):
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when displaying info")


def show_info(parent, message, button_text=None):
    w = InfoWidget(message, button_text)
    d = GreyedDialog(w, title=None, parent=parent, hide_close=True)
    w.dialog = d
    w.button_ok.setFocus()
    return d.exec_()
