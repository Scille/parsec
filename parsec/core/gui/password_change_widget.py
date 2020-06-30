# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtWidgets import QApplication, QWidget
from structlog import get_logger

from parsec.core.gui.custom_dialogs import GreyedDialog, show_error, show_info
from parsec.core.gui.lang import translate as _
from parsec.core.gui.password_validation import PasswordStrengthWidget, get_password_strength
from parsec.core.gui.ui.password_change_widget import Ui_PasswordChangeWidget
from parsec.core.local_device import LocalDeviceCryptoError, change_device_password, get_key_file

logger = get_logger()


class PasswordChangeWidget(QWidget, Ui_PasswordChangeWidget):
    def __init__(self, core, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.core = core
        self.dialog = None
        pwd_str_widget = PasswordStrengthWidget()
        self.line_edit_old_password.textChanged.connect(self.check_infos)
        self.line_edit_password.textChanged.connect(pwd_str_widget.on_password_change)
        self.line_edit_password.textChanged.connect(self.check_infos)
        self.line_edit_password_check.textChanged.connect(self.check_infos)
        self.layout_password_strength.addWidget(pwd_str_widget)
        self.button_change.clicked.connect(self.change_password)
        self.button_change.setDisabled(True)

    def check_infos(self):
        if (
            len(self.line_edit_old_password.text())
            and len(self.line_edit_password.text())
            and get_password_strength(self.line_edit_password.text()) > 0
            and len(self.line_edit_password_check.text())
        ):
            self.button_change.setDisabled(False)
        else:
            self.button_change.setDisabled(True)

    def change_password(self):
        if self.line_edit_password.text() != self.line_edit_password_check.text():
            show_error(self, _("TEXT_CHANGE_PASSWORD_PASSWORD_MISMATCH"))
        else:
            key_file = get_key_file(self.core.config.config_dir, self.core.device)
            try:
                change_device_password(
                    key_file, self.line_edit_old_password.text(), self.line_edit_password.text()
                )
                show_info(self, _("TEXT_CHANGE_PASSWORD_SUCCESS"))
                if self.dialog:
                    self.dialog.accept()
                elif QApplication.activeModalWidget():
                    QApplication.activeModalWidget().accept()
                else:
                    logger.warning("Cannot close dialog when changing password info")
            except LocalDeviceCryptoError as exc:
                show_error(self, _("TEXT_CHANGE_PASSWORD_INVALID_PASSWORD"), exception=exc)

    @classmethod
    def exec_modal(cls, core, parent):
        w = cls(core=core)
        d = GreyedDialog(w, title=_("TEXT_CHANGE_PASSWORD_TITLE"), parent=parent)
        w.dialog = d
        return d.exec_()
