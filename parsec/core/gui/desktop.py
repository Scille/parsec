# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import platform

from ctypes import cdll, c_uint, byref, c_void_p, c_int, c_short

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


native_library = None


def is_caps_lock_on():
    # Apparently, there's no cross-platform way to check if caps lock is on in 2021.
    # Qt doesn't provide any, neither does Python.
    # We load native libraries and call native functions, GetKeyState on Windows
    # and XkbGetIndicatorState on X11 systems.

    global native_library

    if platform.system() == "Windows":
        try:
            if not native_library:
                native_library = cdll.LoadLibrary("User32")
                native_library.GetKeyState.restype = c_short

            VK_CAPITAL = c_int(0x14)
            state = native_library.GetKeyState(VK_CAPITAL)
            return state != 0
        except:
            return False
    else:
        try:
            if not native_library:
                if platform.system() == "Darwin":
                    native_library = cdll.LoadLibrary("libX11.dylib")
                else:
                    native_library = cdll.LoadLibrary("libX11.so")
                if not native_library:
                    return False
                native_library.XOpenDisplay.restype = c_void_p

            state = c_uint()
            XkbUseCoreKdb = c_uint(0x0100)
            CapsLockMask = 0x1
            display = native_library.XOpenDisplay(None)
            if display:
                native_library.XkbGetIndicatorState(display, XkbUseCoreKdb, byref(state))
                native_library.XCloseDisplay(display)
                return state.value & CapsLockMask
            return False
        except:
            return False
