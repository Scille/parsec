# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from unittest.mock import patch
from functools import wraps, partial
import trio
from trio.testing import trio_test as vanilla_trio_test
import queue
import threading
from concurrent import futures

from parsec.event_bus import EventBus
from parsec import __version__ as parsec_version
from parsec.core.gui.main_window import MainWindow
from parsec.core.gui.trio_thread import QtToTrioJobScheduler
from parsec.core.gui.login_widget import LoginWidget
from parsec.core.gui.central_widget import CentralWidget
from parsec.core.gui.lang import switch_language


class ThreadedTrioTestRunner:
    def __init__(self):
        self._thread = None
        self._trio_token = None
        self._request_queue = queue.Queue()
        self._test_result = futures.Future()
        self._job_scheduler = QtToTrioJobScheduler()

        # State events
        self._stopping = None
        self._started = threading.Event()

    def start_test_thread(self, fn, **kwargs):
        async_target = partial(self._test_target, fn)
        sync_target = partial(vanilla_trio_test(async_target), **kwargs)
        self._thread = threading.Thread(target=sync_target)
        self._thread.start()
        self._started.wait()

    def process_requests_until_test_result(self):
        self._process_requests()
        return self._test_result.result()

    def stop_test_thread(self):
        # Set the stopping state event
        trio.from_thread.run_sync(self._stopping.set, trio_token=self._trio_token)
        self._thread.join()

    async def send_action(self, fn, *args, **kwargs):
        reply_sender, reply_receiver = trio.open_memory_channel(1)

        def reply_callback(future):
            trio.from_thread.run_sync(reply_sender.send_nowait, future, trio_token=self._trio_token)

        request = partial(fn, *args, **kwargs)
        self._request_queue.put_nowait((request, reply_callback))
        reply = await reply_receiver.receive()
        return reply.result()

    def _process_requests(self):
        for request, callback in iter(self._request_queue.get, None):
            reply = futures.Future()
            try:
                result = request()
            except Exception as exc:
                reply.set_exception(exc)
            else:
                reply.set_result(result)
            callback(reply)

    async def _run_with_job_scheduler(self, fn, **kwargs):
        async with trio.open_service_nursery() as nursery:
            await nursery.start(self._job_scheduler._start)
            try:
                return await fn(**kwargs)
            finally:
                await self._job_scheduler._stop()

    async def _test_target(self, fn, **kwargs):
        # Initialize trio objects
        self._lock = trio.Lock()
        self._stopping = trio.Event()
        self._trio_token = trio.hazmat.current_trio_token()

        # Set the started state event
        self._started.set()

        # Run the test
        try:
            result = await self._run_with_job_scheduler(fn, **kwargs)
        except BaseException as exc:
            self._test_result.set_exception(exc)
        else:
            self._test_result.set_result(result)

        # Indicate there will be no more requests
        self._request_queue.put_nowait(None)

        # Let the trio loop run until teardown
        await self._stopping.wait()


# Not an async fixture (and doesn't depend on an async fixture either)
# this fixture will actually be executed *before* pytest-trio setup
# the trio loop, giving us a chance to monkeypatch it !
@pytest.fixture
def run_trio_test_in_thread():
    runner = ThreadedTrioTestRunner()

    def trio_test(fn):
        @wraps(fn)
        def wrapper(**kwargs):
            runner.start_test_thread(fn, **kwargs)
            return runner.process_requests_until_test_result()

        return wrapper

    with patch("pytest_trio.plugin.trio_test", new=trio_test):

        yield runner

        # Wait for the last moment before stopping the thread
        runner.stop_test_thread()


@pytest.fixture
async def qt_thread_gateway(run_trio_test_in_thread):
    return run_trio_test_in_thread


@pytest.fixture
async def aqtbot(qtbot, run_trio_test_in_thread):
    return AsyncQtBot(qtbot, run_trio_test_in_thread)


class CtxManagerAsyncWrapped:
    def __init__(self, qtbot, qt_thread_gateway, fnname, *args, **kwargs):
        self.qtbot = qtbot
        self.qt_thread_gateway = qt_thread_gateway
        self.fnname = fnname
        self.args = args
        self.kwargs = kwargs
        self.ctx = None

    async def __aenter__(self):
        def _action_enter():
            self.ctx = getattr(self.qtbot, self.fnname)(*self.args, **self.kwargs)
            self.ctx.__enter__()

        await self.qt_thread_gateway.send_action(_action_enter)

    async def __aexit__(self, exc_type, exc, tb):
        def _action_exit():
            self.ctx.__exit__(exc_type, exc, tb)

        await self.qt_thread_gateway.send_action(_action_exit)


class AsyncQtBot:
    def __init__(self, qtbot, qt_thread_gateway):
        self.qtbot = qtbot
        self.qt_thread_gateway = qt_thread_gateway
        self.run = self.qt_thread_gateway.send_action

        def _autowrap(fnname):
            async def wrapper(*args, **kwargs):
                def _action():
                    return getattr(self.qtbot, fnname)(*args, **kwargs)

                return await self.qt_thread_gateway.send_action(_action)

            wrapper.__name__ = f"{fnname}"
            return wrapper

        self.key_click = _autowrap("keyClick")
        self.key_clicks = _autowrap("keyClicks")
        self.key_event = _autowrap("keyEvent")
        self.key_press = _autowrap("keyPress")
        self.key_release = _autowrap("keyRelease")
        # self.key_to_ascii = self.qtbot.keyToAscii  # available ?
        self.mouse_click = _autowrap("mouseClick")
        self.mouse_d_click = _autowrap("mouseDClick")
        self.mouse_move = _autowrap("mouseMove")
        self.mouse_press = _autowrap("mousePress")
        self.mouse_release = _autowrap("mouseRelease")
        self.wait_for_window_shown = _autowrap("waitForWindowShown")

        self.add_widget = _autowrap("add_widget")
        self.stop = _autowrap("stop")
        self.wait = _autowrap("wait")

        def _autowrap_ctx_manager(fnname):
            def wrapper(*args, **kwargs):
                return CtxManagerAsyncWrapped(
                    self.qtbot, self.qt_thread_gateway, fnname, *args, **kwargs
                )

            wrapper.__name__ = f"{fnname}"
            return wrapper

        self.wait_signal = _autowrap_ctx_manager("wait_signal")
        self.wait_signals = _autowrap_ctx_manager("wait_signals")
        self.assert_not_emitted = _autowrap_ctx_manager("assert_not_emitted")
        self.wait_active = _autowrap_ctx_manager("wait_active")
        self.wait_exposed = _autowrap_ctx_manager("wait_exposed")
        self.capture_exceptions = _autowrap_ctx_manager("capture_exceptions")


@pytest.fixture
def autoclose_dialog(monkeypatch):
    class DialogSpy:
        def __init__(self):
            self.dialogs = []

        def reset(self):
            self.dialogs = []

    spy = DialogSpy()

    def _dialog_exec(dialog):
        if getattr(dialog.center_widget, "label_message", None):
            spy.dialogs.append(
                (dialog.label_title.text(), dialog.center_widget.label_message.text())
            )
        else:
            spy.dialogs.append((dialog.label_title.text(), dialog.center_widget))

    monkeypatch.setattr(
        "parsec.core.gui.custom_dialogs.GreyedDialog.exec_", _dialog_exec, raising=False
    )
    return spy


@pytest.fixture
def gui_factory(qtbot, qt_thread_gateway, core_config, monkeypatch):
    windows = []

    async def _gui_factory(event_bus=None, core_config=core_config, start_arg=None):
        # First start popup blocks the test
        # Check version and mountpoint are useless for most tests
        core_config = core_config.evolve(
            gui_check_version_at_startup=False,
            gui_first_launch=False,
            gui_last_version=parsec_version,
            mountpoint_enabled=False,
            gui_language="en",
        )
        event_bus = event_bus or EventBus()
        # Language config rely on global var, must reset it for each test !
        switch_language(core_config)

        def _create_main_window():
            # Pass minimize_on_close to avoid having test blocked by the
            # closing confirmation prompt

            switch_language(core_config, "en")
            monkeypatch.setattr(
                "parsec.core.gui.main_window.list_available_devices",
                lambda *args, **kwargs: (["a"]),
            )
            main_w = MainWindow(
                qt_thread_gateway._job_scheduler, event_bus, core_config, minimize_on_close=True
            )
            qtbot.add_widget(main_w)
            main_w.showMaximized()
            main_w.show_top()
            windows.append(main_w)
            main_w.add_instance(start_arg)
            qtbot.waitForWindowShown(main_w)
            return main_w

        return await qt_thread_gateway.send_action(_create_main_window)

    return _gui_factory


@pytest.fixture
async def gui(gui_factory, event_bus, core_config):
    return await gui_factory(event_bus, core_config)


# Decorator to add a method to a class
def add_method(cls):
    def _add_method(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            return func(*args, **kwargs)

        setattr(cls, func.__name__, func)
        return func

    return _add_method


# Since widgets are not longer persistent and are instantiated only when needed,
# we can no longer simply access them.
# These methods help to retrieve a widget according to the current state of the GUI.
# They're prefixed by "test_" to ensure that they do not erase any "real" methods.


@add_method(MainWindow)
def test_get_tab(self):
    w = self.tab_center.currentWidget()
    return w


@add_method(MainWindow)
def test_get_main_widget(self):
    tabw = self.test_get_tab()
    item = tabw.layout().itemAt(0)
    return item.widget()


@add_method(MainWindow)
def test_get_central_widget(self):
    main_widget = self.test_get_main_widget()
    if not isinstance(main_widget, CentralWidget):
        return None
    return main_widget


@add_method(MainWindow)
def test_get_login_widget(self):
    main_widget = self.test_get_main_widget()
    if not isinstance(main_widget, LoginWidget):
        return None
    return main_widget


@add_method(MainWindow)
def test_get_users_widget(self):
    central_widget = self.test_get_central_widget()
    return central_widget.users_widget


@add_method(MainWindow)
def test_get_devices_widget(self):
    central_widget = self.test_get_central_widget()
    return central_widget.devices_widget


@add_method(MainWindow)
def test_get_mount_widget(self):
    central_widget = self.test_get_central_widget()
    return central_widget.mount_widget


@add_method(MainWindow)
def test_get_workspaces_widget(self):
    mount_widget = self.test_get_mount_widget()
    return mount_widget.workspaces_widget


@add_method(MainWindow)
def test_get_files_widget(self):
    mount_widget = self.test_get_mount_widget()
    return mount_widget.files_widget
