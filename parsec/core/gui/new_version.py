# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import json
import platform
import re
from urllib.request import Request, urlopen

import trio
from PyQt5.QtCore import QSysInfo, Qt, pyqtSignal
from PyQt5.QtWidgets import QDialog, QWidget

from parsec import __version__
from parsec.core.gui import desktop
from parsec.core.gui.lang import translate as _
from parsec.core.gui.trio_thread import ThreadSafeQtSignal
from parsec.core.gui.ui.new_version_available import Ui_NewVersionAvailable
from parsec.core.gui.ui.new_version_dialog import Ui_NewVersionDialog
from parsec.core.gui.ui.new_version_info import Ui_NewVersionInfo


def _extract_version_tuple(raw):
    match = re.match(r"^.*([0-9]+)\.([0-9]+)\.([0-9]+)", raw)
    if match:
        return tuple(int(x) for x in match.groups())
    else:
        return None


async def _async_get(url, method="GET"):
    return await trio.to_thread.run_sync(lambda: urlopen(Request(url, method=method)))


async def _do_check_new_version(url, api_url):
    # urlopen automatically follows redirections
    resolved_url = (await _async_get(url, method="HEAD")).geturl()
    latest_from_head = _extract_version_tuple(resolved_url)
    current_version = _extract_version_tuple(__version__)
    if latest_from_head and current_version and current_version < latest_from_head:
        json_releases = json.loads((await _async_get(api_url)).read())

        current_arch = QSysInfo().currentCpuArchitecture()
        if current_arch == "x86_64":
            win_version = "win64"
        elif current_arch == "i386":
            win_version = "win32"
        else:
            return (latest_from_head, url)

        latest_version = (0, 0, 0)
        latest_url = ""

        for release in json_releases:
            try:
                if release["draft"]:
                    continue
                if release["prerelease"]:
                    continue
                for asset in release["assets"]:
                    if asset["name"].endswith(f"-{win_version}-setup.exe"):
                        asset_version = _extract_version_tuple(release["tag_name"])
                        if asset_version > latest_version:
                            latest_version = asset_version
                            latest_url = asset["browser_download_url"]
            # In case something went wrong, still better to redirect to GitHub
            except (KeyError, TypeError):
                return (latest_from_head, url)
        if latest_version > current_version:
            return (latest_version, latest_url)
    return None


class NewVersionInfo(QWidget, Ui_NewVersionInfo):
    close_clicked = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.button_close.clicked.connect(self.close_clicked.emit)
        self.show_waiting()

    def show_error(self):
        self.label_waiting.hide()
        self.label_error.show()
        self.label_up_to_date.hide()

    def show_up_to_date(self):
        self.label_waiting.hide()
        self.label_error.hide()
        self.label_up_to_date.show()

    def show_waiting(self):
        self.label_waiting.show()
        self.label_error.hide()
        self.label_up_to_date.hide()


class NewVersionAvailable(QWidget, Ui_NewVersionAvailable):
    download_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.button_download.clicked.connect(self.download_clicked.emit)
        self.button_ignore.clicked.connect(self.close_clicked.emit)

    def set_version(self, version):
        if version:
            self.label.setText(
                _("TEXT_PARSEC_NEW_VERSION_AVAILABLE_version").format(
                    version=".".join([str(_) for _ in version])
                )
            )


class CheckNewVersion(QDialog, Ui_NewVersionDialog):
    check_new_version_success = pyqtSignal()
    check_new_version_error = pyqtSignal()

    def __init__(self, jobs_ctx, event_bus, config, **kwargs):
        super().__init__(**kwargs)
        self.setupUi(self)

        if platform.system() != "Windows":
            return

        self.widget_info = NewVersionInfo(parent=self)
        self.widget_available = NewVersionAvailable(parent=self)
        self.widget_available.hide()
        self.layout.addWidget(self.widget_info)
        self.layout.addWidget(self.widget_available)

        self.widget_info.close_clicked.connect(self.ignore)
        self.widget_available.close_clicked.connect(self.ignore)
        self.widget_available.download_clicked.connect(self.download)

        self.jobs_ctx = jobs_ctx
        self.event_bus = event_bus
        self.config = config

        self.check_new_version_success.connect(self.on_check_new_version_success)
        self.check_new_version_error.connect(self.on_check_new_version_error)

        self.version_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "check_new_version_success"),
            ThreadSafeQtSignal(self, "check_new_version_error"),
            _do_check_new_version,
            url=self.config.gui_check_version_url,
            api_url=self.config.gui_check_version_api_url,
        )
        self.setWindowFlags(Qt.SplashScreen)

    def on_check_new_version_success(self):
        assert self.version_job.is_finished()
        assert self.version_job.status == "ok"
        version_job_ret = self.version_job.ret
        self.version_job = None
        if version_job_ret:
            new_version, url = version_job_ret
            self.widget_available.show()
            self.widget_info.hide()
            self.widget_available.set_version(new_version)
            self.download_url = url
            if not self.isVisible():
                self.exec_()
        else:
            if not self.isVisible():
                self.ignore()
            self.widget_available.hide()
            self.widget_info.show()
            self.widget_info.show_up_to_date()

    def on_check_new_version_error(self):
        assert self.version_job.is_finished()
        assert self.version_job.status != "ok"
        self.version_job = None
        if not self.isVisible():
            self.ignore()
        self.widget_available.hide()
        self.widget_info.show()
        self.widget_info.show_error()

    def download(self):
        desktop.open_url(self.download_url)
        self.accept()

    def ignore(self):
        self.reject()

    def closeEvent(self, event):
        if self.version_job:
            self.version_job.cancel_and_join()
        event.accept()
