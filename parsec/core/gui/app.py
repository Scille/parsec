# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import sys
import signal
from queue import Queue
from typing import Optional
from contextlib import contextmanager
from enum import Enum
import trio
from structlog import get_logger
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication

from parsec.event_bus import EventBus
from parsec.core.core_events import CoreEvent
from parsec.core.config import CoreConfig
from parsec.core.ipcinterface import (
    run_ipc_server,
    send_to_ipc_server,
    IPCServerAlreadyRunning,
    IPCServerNotRunning,
    IPCCommand,
)

try:
    from parsec.core.gui import lang
    from parsec.core.gui.parsec_application import ParsecApp
    from parsec.core.gui.new_version import CheckNewVersion
    from parsec.core.gui.systray import systray_available, Systray
    from parsec.core.gui.main_window import MainWindow
    from parsec.core.gui.trio_thread import ThreadSafeQtSignal, run_trio_thread
except ImportError as exc:
    raise ModuleNotFoundError(
        """PyQt forms haven't been generated.
You must install the parsec package or run `python setup.py generate_pyqt_forms`
"""
    ) from exc


logger = get_logger()


def before_quit(systray):
    def _before_quit():
        systray.hide()

    return _before_quit


IPCServerStartupOutcome = Enum("IPCServerStartupOutcome", "STARTED ALREADY_RUNNING ERROR")


async def _run_ipc_server(config, main_window, start_arg, result_queue):
    try:
        new_instance_needed_qt = ThreadSafeQtSignal(main_window, "new_instance_needed", object)
        foreground_needed_qt = ThreadSafeQtSignal(main_window, "foreground_needed")

        async def _cmd_handler(cmd):
            if cmd["cmd"] == IPCCommand.FOREGROUND:
                foreground_needed_qt.emit()
            elif cmd["cmd"] == IPCCommand.NEW_INSTANCE:
                new_instance_needed_qt.emit(cmd.get("start_arg"))
            return {"status": "ok"}

        while True:
            try:
                async with run_ipc_server(
                    _cmd_handler,
                    config.ipc_socket_file,
                    win32_mutex_name=config.ipc_win32_mutex_name,
                ):
                    result_queue.put_nowait(IPCServerStartupOutcome.STARTED)
                    await trio.sleep_forever()

            except IPCServerAlreadyRunning:
                # Parsec is already started, give it our work then
                try:
                    if start_arg:
                        await send_to_ipc_server(
                            config.ipc_socket_file, IPCCommand.NEW_INSTANCE, start_arg=start_arg
                        )
                    else:
                        await send_to_ipc_server(config.ipc_socket_file, IPCCommand.FOREGROUND)

                except IPCServerNotRunning:
                    # IPC server has closed, retry to create our own
                    continue

                # We have successfuly noticed the other running application
                result_queue.put_nowait(IPCServerStartupOutcome.ALREADY_RUNNING)
                break

    except Exception:
        result_queue.put_nowait(IPCServerStartupOutcome.ERROR)
        # Let the exception bubble up so QtToTrioJob logged it as an unexpected error
        raise


@contextmanager
def fail_on_first_exception(kill_window):
    exceptions = []

    def excepthook(etype, exception, traceback):
        exceptions.append(exception)
        kill_window()
        return previous_hook(etype, exception, traceback)

    sys.excepthook, previous_hook = excepthook, sys.excepthook

    try:
        yield
    finally:
        sys.excepthook = previous_hook
        if exceptions:
            raise exceptions[0]


@contextmanager
def log_pyqt_exceptions():
    # Override sys.excepthook to be able to properly log exceptions occuring in Qt slots.
    # Exceptions occuring in the core while in the Qt app should be catched sooner by the
    # job.

    def log_except(etype, exception, traceback):
        logger.exception("Exception in Qt slot", exc_info=(etype, exception, traceback))

    sys.excepthook, previous_hook = log_except, sys.excepthook
    try:
        yield
    finally:
        sys.excepthook = previous_hook


def run_gui(config: CoreConfig, start_arg: Optional[str] = None, diagnose: bool = False):
    logger.info("Starting UI")

    # Needed for High DPI usage of QIcons, otherwise only QImages are well scaled
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = ParsecApp()

    app.load_stylesheet()
    app.load_font()

    lang_key = lang.switch_language(config)

    event_bus = EventBus()
    with run_trio_thread() as jobs_ctx:
        win = MainWindow(
            jobs_ctx=jobs_ctx,
            event_bus=event_bus,
            config=config,
            minimize_on_close=config.gui_tray_enabled and systray_available(),
        )

        result_queue = Queue(maxsize=1)

        class ThreadSafeNoQtSignal(ThreadSafeQtSignal):
            def __init__(self):
                self.qobj = None
                self.signal_name = ""
                self.args_types = ()

            def emit(self, *args):
                pass

        jobs_ctx.submit_job(
            ThreadSafeNoQtSignal(),
            ThreadSafeNoQtSignal(),
            _run_ipc_server,
            config,
            win,
            start_arg,
            result_queue,
        )
        if result_queue.get() == IPCServerStartupOutcome.ALREADY_RUNNING:
            # Another instance of Parsec already started, nothing more to do
            return

        # If we are here, it's either the IPC server has successfully started
        # or it has crashed without being able to communicate with an existing
        # IPC server. Such case is of course not supposed to happen but if it
        # does we nevertheless keep the application running as a kind of
        # failsafe mode (and the crash reason is logged and sent to telemetry).

        # Systray is not displayed on MacOS, having natively a menu with similar functions.
        if systray_available() and sys.platform != "darwin":
            systray = Systray(parent=win)
            win.systray_notification.connect(systray.on_systray_notification)
            systray.on_close.connect(win.close_app)
            systray.on_show.connect(win.show_top)
            app.aboutToQuit.connect(before_quit(systray))
            if config.gui_tray_enabled:
                app.setQuitOnLastWindowClosed(False)

        if config.gui_check_version_at_startup and not diagnose:
            CheckNewVersion(jobs_ctx=jobs_ctx, event_bus=event_bus, config=config, parent=win)

        win.show_window(skip_dialogs=diagnose, invitation_link=start_arg)
        win.show_top()
        win.new_instance_needed.emit(start_arg)

        if sys.platform == "darwin":
            from parsec.core.gui.instance_widget import macfuse_installation_widget

            macfuse_installation_widget(win)

        def kill_window(*args):
            win.close_app(force=True)
            QApplication.quit()

        signal.signal(signal.SIGINT, kill_window)

        # QTimer wakes up the event loop periodically which allows us to close
        # the window even when it is in background.
        timer = QTimer()
        timer.start(400)
        timer.timeout.connect(lambda: None)

        if diagnose:
            diagnose_timer = QTimer()
            diagnose_timer.start(1000)
            diagnose_timer.timeout.connect(kill_window)

        if lang_key:
            event_bus.send(CoreEvent.GUI_CONFIG_CHANGED, gui_language=lang_key)

        if diagnose:
            with fail_on_first_exception(kill_window):
                return app.exec_()
        else:
            with log_pyqt_exceptions():
                return app.exec_()
