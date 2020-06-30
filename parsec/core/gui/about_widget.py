# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtWidgets import QWidget

from parsec import __version__
from parsec.core.gui.lang import translate as _
from parsec.core.gui.ui.about_widget import Ui_AboutWidget


class AboutWidget(QWidget, Ui_AboutWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.label_version.setText(_("TEXT_PARSEC_VERSION_version").format(version=__version__))
