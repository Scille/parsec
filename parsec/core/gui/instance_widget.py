# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from parsec.core.config import CoreConfig
from typing import Optional
from parsec.core.core_events import CoreEvent
import trio
import sys
from structlog import get_logger
from PyQt5.QtCore import pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QApplication

from parsec.event_bus import EventBus
from parsec.api.protocol import HandshakeRevokedDevice
from parsec.core import logged_core_factory
from parsec.core.local_device import LocalDeviceError, load_device_with_password
from parsec.core.mountpoint import (
    MountpointConfigurationError,
    MountpointDriverCrash,
    MountpointFuseNotAvailable,
    MountpointWinfspNotAvailable,
)

from parsec.core.gui.trio_thread import QtToTrioJobScheduler, run_trio_job_scheduler
from parsec.core.gui.parsec_application import ParsecApp
from parsec.core.gui.custom_dialogs import show_error, show_info_link
from parsec.core.gui.lang import translate as _
from parsec.core.gui.login_widget import LoginWidget
from parsec.core.gui.central_widget import CentralWidget


logger = get_logger()


async def _do_run_core(config, device, qt_on_ready):
    # Quick fix to avoid MultiError<Cancelled, ...> exception bubbling up
    # TODO: replace this by a proper generic MultiError handling
    with trio.MultiError.catch(lambda exc: None if isinstance(exc, trio.Cancelled) else exc):
        async with logged_core_factory(config=config, device=device, event_bus=None) as core:
            # Create our own job scheduler allows us to cancel all pending
            # jobs depending on us when we logout
            async with run_trio_job_scheduler() as core_jobs_ctx:
                qt_on_ready.emit(core, core_jobs_ctx)
                await trio.sleep_forever()


class InstanceWidget(QWidget):
    run_core_success = pyqtSignal()
    run_core_error = pyqtSignal()
    run_core_ready = pyqtSignal(object, object)
    logged_in = pyqtSignal()
    logged_out = pyqtSignal()
    state_changed = pyqtSignal(QWidget, str)
    login_failed = pyqtSignal()
    join_organization_clicked = pyqtSignal()
    create_organization_clicked = pyqtSignal()

    def __init__(
        self,
        jobs_ctx: QtToTrioJobScheduler,
        event_bus: EventBus,
        config: CoreConfig,
        systray_notification: pyqtSignal,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.jobs_ctx = jobs_ctx
        self.event_bus = event_bus
        self.config = config
        self.systray_notification = systray_notification

        self.core = None
        self.core_jobs_ctx = None
        self.running_core_job = None
        self.workspace_path = None

        self.run_core_success.connect(self.on_core_run_done)
        self.run_core_error.connect(self.on_core_run_error)
        self.run_core_ready.connect(self.on_run_core_ready)
        self.logged_in.connect(self.on_logged_in)
        self.logged_out.connect(self.on_logged_out)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

    @pyqtSlot(object, object)
    def _core_ready(self, core, core_jobs_ctx):
        self.run_core_ready.emit(core, core_jobs_ctx)

    @property
    def current_device(self):
        if self.core:
            return self.core.device
        return None

    @property
    def is_logged_in(self):
        return self.running_core_job is not None

    def set_workspace_path(self, action_addr):
        self.workspace_path = action_addr

    def reset_workspace_path(self):
        self.workspace_path = None

    def on_core_config_updated(self, event, **kwargs):
        self.event_bus.send(CoreEvent.GUI_CONFIG_CHANGED, **kwargs)

    def start_core(self, device):
        assert not self.running_core_job
        assert not self.core
        assert not self.core_jobs_ctx

        self.config = ParsecApp.get_main_window().config

        self.running_core_job = self.jobs_ctx.submit_job(
            self.run_core_success,
            self.run_core_error,
            _do_run_core,
            self.config,
            device,
            self.run_core_ready,
        )

    def on_run_core_ready(self, core, core_jobs_ctx):
        self.core = core
        self.core_jobs_ctx = core_jobs_ctx
        self.core.event_bus.connect(CoreEvent.GUI_CONFIG_CHANGED, self.on_core_config_updated)
        self.event_bus.send(
            CoreEvent.GUI_CONFIG_CHANGED,
            gui_last_device="{}:{}".format(
                self.core.device.organization_addr.organization_id, self.core.device.device_id
            ),
        )
        ParsecApp.add_connected_device(
            self.core.device.organization_addr.organization_id, self.core.device.device_id
        )
        self.logged_in.emit()

    def on_core_run_error(self):
        assert self.running_core_job.is_finished()
        if self.core:
            self.core.event_bus.disconnect(
                CoreEvent.GUI_CONFIG_CHANGED, self.on_core_config_updated
            )
        if self.running_core_job.status is not None:
            if isinstance(self.running_core_job.exc, HandshakeRevokedDevice):
                show_error(
                    self, _("TEXT_LOGIN_ERROR_DEVICE_REVOKED"), exception=self.running_core_job.exc
                )
            elif isinstance(self.running_core_job.exc, MountpointWinfspNotAvailable):
                show_error(
                    self,
                    _("TEXT_LOGIN_ERROR_WINFSP_NOT_AVAILABLE"),
                    exception=self.running_core_job.exc,
                )
            elif isinstance(self.running_core_job.exc, MountpointFuseNotAvailable):
                if sys.platform == "darwin":
                    show_info_link(
                        self,
                        _("TEXT_MACFUSE_INSTALL_WINDOW_TITLE"),
                        _("TEXT_LOGIN_ERROR_MACFUSE_NOT_AVAILABLE"),
                        _("TEXT_MACFUSE_DOWNLOAD_BUTTON"),
                        "https://osxfuse.github.io",
                    )
                else:
                    show_error(
                        self,
                        _("TEXT_LOGIN_ERROR_FUSE_NOT_AVAILABLE"),
                        exception=self.running_core_job.exc,
                    )
            else:
                logger.exception("Unhandled error", exc_info=self.running_core_job.exc)
                show_error(self, _("TEXT_LOGIN_UNKNOWN_ERROR"), exception=self.running_core_job.exc)
        self.running_core_job = None
        self.logged_out.emit()

    def on_core_run_done(self):
        assert self.running_core_job.is_finished()
        if self.core:
            ParsecApp.remove_connected_device(
                self.core.device.organization_addr.organization_id, self.core.device.device_id
            )
            self.core.event_bus.disconnect(
                CoreEvent.GUI_CONFIG_CHANGED, self.on_core_config_updated
            )
        self.running_core_job = None
        self.logged_out.emit()

    def stop_core(self):
        if self.running_core_job:
            self.running_core_job.cancel()

    def on_logged_out(self):
        self.state_changed.emit(self, "logout")
        self.show_login_widget()

    def on_logged_in(self):
        self.state_changed.emit(self, "connected")
        self.show_central_widget()

    def logout(self):
        self.stop_core()

    def login_with_password(self, key_file, password):
        message = None
        exception = None
        try:
            device = load_device_with_password(key_file, password)
            if ParsecApp.is_device_connected(
                device.organization_addr.organization_id, device.device_id
            ):
                message = _("TEXT_LOGIN_ERROR_ALREADY_CONNECTED")
            else:
                self.start_core(device)
        except LocalDeviceError as exc:
            message = _("TEXT_LOGIN_ERROR_AUTHENTICATION_FAILED")
            exception = exc

        except (RuntimeError, MountpointConfigurationError, MountpointDriverCrash) as exc:
            message = _("TEXT_LOGIN_MOUNTPOINT_ERROR")
            exception = exc

        except Exception as exc:
            message = _("TEXT_LOGIN_UNKNOWN_ERROR")
            exception = exc
            logger.exception("Unhandled error during login")
        finally:
            if message:
                show_error(self, message, exception=exception)
                self.login_failed.emit()

    def show_central_widget(self):
        self.clear_widgets()
        # The core can be set to None at any time if do_run_core get an error, is cancelled or
        # terminate.
        core = self.core
        core_jobs_ctx = self.core_jobs_ctx
        if core is None or core_jobs_ctx is None:
            return
        central_widget = CentralWidget(
            core=core,
            jobs_ctx=core_jobs_ctx,
            event_bus=core.event_bus,
            systray_notification=self.systray_notification,
            file_link_addr=self.workspace_path,
            parent=self,
        )
        self.layout().addWidget(central_widget)
        central_widget.logout_requested.connect(self.logout)
        central_widget.show()

    def show_login_widget(self):
        self.clear_widgets()
        login_widget = LoginWidget(
            self.jobs_ctx, self.event_bus, self.config, self.login_failed, parent=self
        )
        self.layout().addWidget(login_widget)

        login_widget.login_with_password_clicked.connect(self.login_with_password)
        login_widget.join_organization_clicked.connect(self.join_organization_clicked.emit)
        login_widget.create_organization_clicked.connect(self.create_organization_clicked.emit)
        login_widget.login_canceled.connect(self.reset_workspace_path)
        login_widget.show()

    def get_central_widget(self) -> Optional[CentralWidget]:
        item = self.layout().itemAt(0)
        if item:
            if isinstance(item.widget(), CentralWidget):
                return item.widget()
        return None

    def get_login_widget(self):
        item = self.layout().itemAt(0)
        if item:
            if isinstance(item.widget(), LoginWidget):
                return item.widget()
        return None

    def clear_widgets(self):
        item = self.layout().takeAt(0)
        if item:
            item.widget().hide()
            item.widget().setParent(None)
        QApplication.processEvents()
