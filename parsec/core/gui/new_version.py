# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import sys
from marshmallow.decorators import post_load
import trio
import json
from typing import Optional, Tuple, List
from json import JSONDecodeError
from urllib.request import urlopen, Request, URLError
from packaging.version import Version
from structlog import get_logger

from PyQt5.QtCore import Qt, pyqtSignal, QSysInfo
from PyQt5.QtWidgets import QDialog, QWidget

from parsec import __version__
from parsec.serde import BaseSchema, fields, JSONSerializer, SerdeError
from parsec.core.gui import desktop
from parsec.core.gui.lang import translate as _
from parsec.core.gui.ui.new_version_dialog import Ui_NewVersionDialog
from parsec.core.gui.ui.new_version_info import Ui_NewVersionInfo
from parsec.core.gui.ui.new_version_available import Ui_NewVersionAvailable


logger = get_logger()


# Helper to make mock easier for testing
def get_platform_and_arch() -> Tuple[str, str]:
    return sys.platform, QSysInfo().currentCpuArchitecture()


class ApiReleaseAssetSchema(BaseSchema):
    name = fields.String(required=True)
    browser_download_url = fields.String(required=True)


class ApiReleaseSchema(BaseSchema):
    draft = fields.Boolean(missing=False)
    prerelease = fields.Boolean(missing=False)
    tag_name = fields.String(required=True)
    assets = fields.List(fields.Nested(ApiReleaseAssetSchema), missing=[])

    @post_load
    def add_version_field(self, data):
        try:
            data["version"] = Version(data["tag_name"])
        except ValueError:
            data["version"] = None
        return data


class ApiReleasesSchema(BaseSchema):
    releases = fields.List(fields.Nested(ApiReleaseSchema), missing=[])


api_releases_serializer = JSONSerializer(ApiReleasesSchema)


async def fetch_json_releases(api_url: str) -> Optional[List]:
    def _do_http_request():
        try:
            with urlopen(Request(api_url, method="GET")) as req_api:
                return json.loads(req_api.read())
        except JSONDecodeError as exc:
            logger.error("Cannot deserialize releases info from API", exc_info=exc, api_url=api_url)
            return None
        except URLError:
            return None

    return await trio.to_thread.run_sync(_do_http_request)


async def do_check_new_version(
    api_url: str, allow_prerelease: bool = False
) -> Optional[Tuple[Version, str]]:
    # Retrieve the releases from Github
    json_releases = await fetch_json_releases(api_url)
    if json_releases is None:
        # Cannot retreive the releases (typically due to no internet access)
        return None

    # We'd better be very careful on the JSON data loading, otherwise a
    # small change could break our ability to detect new version...
    # hence dooming the user to never upgrade !
    try:
        releases = api_releases_serializer.load({"releases": json_releases})
    except SerdeError as exc:
        logger.error("Cannot load releases info from API", exc_info=exc, api_url=api_url)
        return None

    # Github return the last 30 releases sorted according to their `created_at`
    # attribute, in theory this order could differ from the release naming order
    # (e.g. v1.1.0 is released, then v1.0.0 release is removed by mistake and
    # recreated).
    # So we force order by version just to be sure. Of course this means we
    # cannot save mistakes that occured after the 30 first releases, but it
    # should be "good enough" ;-)
    # We also filter out tags which are not named after a version, this is just
    # an extra sanity as they shouldn't be marked as released in the first place.
    releases = sorted(
        [r for r in releases["releases"] if r["version"] is not None],
        key=lambda r: r["version"],
        reverse=True,
    )

    # Filter the releases to find the most recent compatible one
    current_version = Version(__version__)
    platform, arch = get_platform_and_arch()
    if platform == "win32" and arch == "x86_64":
        installer_suffix = "-win64-setup.exe"
    elif platform == "win32" and arch == "i386":
        installer_suffix = "-win32-setup.exe"
    elif platform == "darwin" and arch == "x86_64":
        installer_suffix = "-macos-amd64.dmg"
    for release in releases:
        if release["version"] <= current_version:
            # Given releases are order, there is no more update candidate releases
            break
        if release["draft"]:
            continue
        if release["prerelease"] and not allow_prerelease:
            continue
        for asset in release["assets"]:
            if asset["name"].endswith(installer_suffix):
                # Given releases are ordered, we have found the latest compatible version
                return release["version"], asset["browser_download_url"]
        # If we are here, a newer version exists BUT it isn't compatible with
        # our platform/arch. Just pretend we saw nothing.

    # No new releases found, we are up to date \o/
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
                _("TEXT_PARSEC_NEW_VERSION_AVAILABLE_version").format(version=str(version))
            )


class CheckNewVersion(QDialog, Ui_NewVersionDialog):
    check_new_version_success = pyqtSignal()
    check_new_version_error = pyqtSignal()

    def __init__(self, jobs_ctx, event_bus, config, **kwargs):
        super().__init__(**kwargs)
        self.setupUi(self)

        if get_platform_and_arch()[0] not in ("win32", "darwin"):
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
            self.check_new_version_success,
            self.check_new_version_error,
            do_check_new_version,
            api_url=self.config.gui_check_version_api_url,
            allow_prerelease=self.config.gui_check_version_allow_pre_release,
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
