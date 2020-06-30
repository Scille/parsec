# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtWidgets import QWidget
from zxcvbn import zxcvbn

from parsec.core.gui.lang import translate as _
from parsec.core.gui.ui.password_strength_widget import Ui_PasswordStrengthWidget

PASSWORD_CSS = {
    0: "color: #333333; background-color: none;",
    1: "color: #F44336; background-color: none;",
    2: "color: #F44336; background-color: none;",
    3: "color: #F1962B; background-color: none;",
    4: "color: #8BC34A; background-color: none;",
    5: "color: #8BC34A; background-color: none;",
}


def get_password_strength_text(password_score):
    PASSWORD_STRENGTH_TEXTS = [
        _("TEXT_PASSWORD_VALIDATION_TOO_SHORT"),
        _("TEXT_PASSWORD_VALIDATION_VERY_WEAK"),
        _("TEXT_PASSWORD_VALIDATION_WEAK"),
        _("TEXT_PASSWORD_VALIDATION_AVERAGE"),
        _("TEXT_PASSWORD_VALIDATION_GOOD"),
        _("TEXT_PASSWORD_VALIDATION_EXCELLENT"),
    ]
    return PASSWORD_STRENGTH_TEXTS[password_score]


def get_password_strength(password):
    if len(password) < 8:
        return 0
    result = zxcvbn(password)
    return result["score"] + 1


class PasswordStrengthWidget(QWidget, Ui_PasswordStrengthWidget):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

    def on_password_change(self, text):
        if not text:
            self.hide()
            return
        score = get_password_strength(text)
        self.label.setText(
            _("TEXT_PASSWORD_VALIDATION_PASSWORD_STRENGTH_strength").format(
                strength=get_password_strength_text(score)
            )
        )
        self.label.setStyleSheet(PASSWORD_CSS[score])
        self.show()
