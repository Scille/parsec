# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from PyQt5.QtCore import QUrl, QFileInfo, QSysInfo, QLocale
from PyQt5.QtGui import QDesktopServices, QGuiApplication, QClipboard

from parsec.api.protocol import DeviceName


def open_file(path):
    return QDesktopServices.openUrl(QUrl.fromLocalFile(QFileInfo(path).absoluteFilePath()))


def open_url(url):
    return QDesktopServices.openUrl(QUrl(url))


def open_doc_link():
    return open_url("https://parsec-cloud.readthedocs.io")


def open_feedback_link():
    return open_url("https://my.parsec.cloud/feedback")


def open_user_guide():
    return open_url("https://parsec.cloud")


def get_default_device():
    device = QSysInfo.machineHostName()
    if device.lower() == "localhost":
        device = QSysInfo.productType()
    return "".join([c for c in device if DeviceName.regex.match(c)])


def get_locale_language():
    return QLocale.system().name()[:2].lower()


def copy_to_clipboard(text):
    QGuiApplication.clipboard().setText(text, QClipboard.Clipboard)
    QGuiApplication.clipboard().setText(text, QClipboard.Selection)


def get_clipboard():
    return QGuiApplication.clipboard().text()
