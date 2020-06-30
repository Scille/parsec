# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import psutil
from PyQt5.QtCore import QFileInfo, QLocale, QSysInfo, QUrl
from PyQt5.QtGui import QClipboard, QDesktopServices, QGuiApplication

from parsec.api.protocol import DeviceName


def open_file(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(QFileInfo(path).absoluteFilePath()))


def open_url(url):
    QDesktopServices.openUrl(QUrl(url))


def open_doc_link():
    open_url("https://parsec-cloud.readthedocs.io")


def open_feedback_link():
    open_url("https://my.parsec.cloud/feedback")


def open_user_guide():
    open_url("https://parsec.cloud")


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


def is_process_running(pid):
    return psutil.pid_exists(pid)


def parsec_instances_count():
    inst_count = 0
    for proc in psutil.process_iter():
        try:
            if proc.name().lower() in ["parsec", "parsec.exe"] and "backend" not in proc.cmdline():
                inst_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return inst_count
