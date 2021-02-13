# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from unittest.mock import patch
from functools import wraps, partial
import trio
from trio.testing import trio_test as vanilla_trio_test
import queue
import threading
from concurrent import futures
from importlib import import_module
from PyQt5 import QtCore

from parsec import __version__ as parsec_version
from parsec.core.local_device import save_device_with_password
from parsec.core.gui.main_window import MainWindow
from parsec.core.gui.workspaces_widget import WorkspaceButton
from parsec.core.gui.trio_thread import QtToTrioJobScheduler
from parsec.core.gui.login_widget import LoginWidget, LoginPasswordInputWidget, LoginAccountsWidget
from parsec.core.gui.central_widget import CentralWidget
from parsec.core.gui.lang import switch_language
from parsec.core.gui.parsec_application import ParsecApp
from parsec.core.local_device import LocalDeviceAlreadyExistsError


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
        self._stopping = trio.Event()
        self._trio_token = trio.lowlevel.current_trio_token()

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

        self.add_widget = _autowrap("add_widget")
        self.stop = _autowrap("stop")
        self.wait = _autowrap("wait")
        self.wait_until = _autowrap("wait_until")

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
    monkeypatch.setattr(
        "parsec.core.gui.custom_dialogs.GreyedDialog.open", _dialog_exec, raising=False
    )
    return spy


@pytest.fixture
def widget_catcher_factory(aqtbot, monkeypatch):
    """Useful to capture lazily created widget such as modals"""

    def _widget_catcher_factory(*widget_cls_pathes, timeout=1000):
        widgets = []

        def _catch_init(self, *args, **kwargs):
            widgets.append(self)
            return self.vanilla__init__(*args, **kwargs)

        for widget_cls_path in widget_cls_pathes:
            module_path, widget_cls_name = widget_cls_path.rsplit(".", 1)
            widget_cls = getattr(import_module(module_path), widget_cls_name)
            monkeypatch.setattr(
                f"{widget_cls_path}.vanilla__init__", widget_cls.__init__, raising=False
            )
            monkeypatch.setattr(f"{widget_cls_path}.__init__", _catch_init)

        async def _wait_next():
            def _invitation_shown():
                assert len(widgets)

            await aqtbot.wait_until(_invitation_shown, timeout=timeout)
            return widgets.pop(0)

        return _wait_next

    return _widget_catcher_factory


@pytest.fixture
def gui_factory(
    aqtbot,
    qtbot,
    qt_thread_gateway,
    testing_main_window_cls,
    core_config,
    monkeypatch,
    event_bus_factory,
):
    windows = []

    async def _gui_factory(
        event_bus=None, core_config=core_config, start_arg=None, skip_dialogs=True
    ):
        # First start popup blocks the test
        # Check version and mountpoint are useless for most tests
        core_config = core_config.evolve(
            gui_check_version_at_startup=False,
            gui_first_launch=False,
            gui_last_version=parsec_version,
            mountpoint_enabled=True,
            gui_language="en",
            gui_show_confined=False,
        )
        event_bus = event_bus or event_bus_factory()
        # Language config rely on global var, must reset it for each test !
        switch_language(core_config)

        ParsecApp.connected_devices = set()

        def _create_main_window():
            # Pass minimize_on_close to avoid having test blocked by the
            # closing confirmation prompt

            switch_language(core_config, "en")

            main_w = testing_main_window_cls(
                qt_thread_gateway._job_scheduler, event_bus, core_config, minimize_on_close=True
            )
            qtbot.add_widget(main_w)
            main_w.show_window(skip_dialogs=skip_dialogs)
            main_w.show_top()
            windows.append(main_w)
            main_w.add_instance(start_arg)

            def right_main_window():
                assert ParsecApp.get_main_window() is main_w

            # For some reasons, the main window from the previous test might
            # still be around. Simply wait for things to settle down until
            # our freshly created window is detected as the app main window.
            qtbot.wait_until(right_main_window)

            return main_w

        return await qt_thread_gateway.send_action(_create_main_window)

    return _gui_factory


@pytest.fixture
async def gui(aqtbot, gui_factory, event_bus, core_config):
    _gui = await gui_factory(event_bus, core_config)

    def _gui_displayed():
        assert _gui.isVisible()
        assert not _gui.isHidden()

    await aqtbot.wait_until(_gui_displayed)
    return _gui


@pytest.fixture
async def logged_gui(aqtbot, gui_factory, core_config, alice, bob, fixtures_customization):
    # Logged as bob (i.e. standard profile) by default
    if fixtures_customization.get("logged_gui_as_admin", False):
        device = alice
    else:
        device = bob

    save_device_with_password(core_config.config_dir, device, "P@ssw0rd")

    gui = await gui_factory()
    await gui.test_switch_to_logged_in(device)
    return gui


@pytest.fixture
def testing_main_window_cls(aqtbot, qt_thread_gateway):
    # Since widgets are not longer persistent and are instantiated only when needed,
    # we can no longer simply access them.
    # These methods help to retrieve a widget according to the current state of the GUI.
    # They're prefixed by "test_" to ensure that they do not erase any "real" methods.

    class TestingMainWindow(MainWindow):
        def test_get_tab(self):
            w = self.tab_center.currentWidget()
            return w

        def test_get_main_widget(self):
            tabw = self.test_get_tab()
            item = tabw.layout().itemAt(0)
            return item.widget()

        def test_get_central_widget(self):
            main_widget = self.test_get_main_widget()
            if not isinstance(main_widget, CentralWidget):
                return None
            return main_widget

        def test_get_login_widget(self):
            main_widget = self.test_get_main_widget()
            if not isinstance(main_widget, LoginWidget):
                return None
            return main_widget

        def test_get_users_widget(self):
            central_widget = self.test_get_central_widget()
            return central_widget.users_widget

        def test_get_devices_widget(self):
            central_widget = self.test_get_central_widget()
            return central_widget.devices_widget

        def test_get_mount_widget(self):
            central_widget = self.test_get_central_widget()
            return central_widget.mount_widget

        def test_get_workspaces_widget(self):
            mount_widget = self.test_get_mount_widget()
            return mount_widget.workspaces_widget

        def test_get_files_widget(self, timer=100):
            mount_widget = self.test_get_mount_widget()
            mount_widget.files_widget.update_timer.setInterval(timer)
            return mount_widget.files_widget

        def test_get_core(self):
            return self.test_get_central_widget().core

        async def test_logout(self):
            central_widget = self.test_get_central_widget()

            def _trigger_logout_menu():
                central_widget.button_user.menu().actions()[2].trigger()

            tabw = self.test_get_tab()

            async with aqtbot.wait_signal(tabw.logged_out):
                await qt_thread_gateway.send_action(_trigger_logout_menu)

            def _wait_logged_out():
                assert not central_widget.isVisible()
                l_w = self.test_get_login_widget()
                assert l_w is not None
                assert l_w.isVisible()

            await aqtbot.wait_until(_wait_logged_out)

        async def test_logout_and_switch_to_login_widget(self):
            await self.test_logout()
            return self.test_get_login_widget()

        async def test_proceed_to_login(self, password, error=False):
            l_w = self.test_get_login_widget()

            accounts_w = l_w.widget.layout().itemAt(0).widget()
            tabw = self.test_get_tab()

            if isinstance(accounts_w, LoginAccountsWidget):
                async with aqtbot.wait_signal(accounts_w.account_clicked):
                    await aqtbot.mouse_click(
                        accounts_w.accounts_widget.layout().itemAt(0).widget(), QtCore.Qt.LeftButton
                    )

            def _password_widget_shown():
                assert isinstance(l_w.widget.layout().itemAt(0).widget(), LoginPasswordInputWidget)

            await aqtbot.wait_until(_password_widget_shown)
            password_w = l_w.widget.layout().itemAt(0).widget()
            await aqtbot.key_clicks(password_w.line_edit_password, password)

            signal = tabw.logged_in if not error else tabw.login_failed
            async with aqtbot.wait_signals([l_w.login_with_password_clicked, signal]):
                await aqtbot.mouse_click(password_w.button_login, QtCore.Qt.LeftButton)

            def _wait_logged_in():
                assert not l_w.isVisible()
                c_w = self.test_get_central_widget()
                assert c_w.isVisible()

            if not error:
                await aqtbot.wait_until(_wait_logged_in)
            return self.test_get_central_widget()

        async def test_switch_to_devices_widget(self, error=False):
            central_widget = self.test_get_central_widget()
            d_w = self.test_get_devices_widget()
            signal = d_w.list_error if error else d_w.list_success
            async with aqtbot.wait_exposed(d_w), aqtbot.wait_signal(signal, timeout=3000):
                await aqtbot.mouse_click(central_widget.menu.button_devices, QtCore.Qt.LeftButton)
            return d_w

        async def test_switch_to_users_widget(self, error=False):
            central_widget = self.test_get_central_widget()
            u_w = self.test_get_users_widget()
            signal = u_w.list_error if error else u_w.list_success
            async with aqtbot.wait_exposed(u_w), aqtbot.wait_signal(signal, timeout=3000):
                await aqtbot.mouse_click(central_widget.menu.button_users, QtCore.Qt.LeftButton)
            return u_w

        async def test_switch_to_workspaces_widget(self, error=False):
            central_widget = self.test_get_central_widget()
            w_w = self.test_get_workspaces_widget()
            signal = w_w.list_error if error else w_w.list_success
            async with aqtbot.wait_exposed(w_w), aqtbot.wait_signal(signal, timeout=3000):
                await aqtbot.mouse_click(central_widget.menu.button_files, QtCore.Qt.LeftButton)
            return w_w

        async def test_switch_to_files_widget(self, workspace_name, error=False):
            w_w = await self.test_switch_to_workspaces_widget()

            for i in range(w_w.layout_workspaces.count()):
                wk_button = w_w.layout_workspaces.itemAt(0).widget()
                if isinstance(wk_button, WorkspaceButton) and wk_button.name == workspace_name:
                    break
            else:
                raise AssertionError(f"Workspace `{workspace_name}` not found")

            f_w = self.test_get_files_widget()
            async with aqtbot.wait_exposed(f_w), aqtbot.wait_signal(f_w.folder_changed):
                await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)

            # Sanity check just to be sure...
            def _entry_available():
                assert f_w.workspace_fs.get_workspace_name() == workspace_name
                assert f_w.table_files.rowCount() == 1

            await aqtbot.wait_until(_entry_available)

            return f_w

        async def test_switch_to_logged_in(self, device):
            try:
                save_device_with_password(self.config.config_dir, device, "P@ssw0rd")
            except LocalDeviceAlreadyExistsError:
                pass

            lw = self.test_get_login_widget()
            # Reload to take into account the new saved device
            await aqtbot.run(lw.reload_devices)
            tabw = self.test_get_tab()

            accounts_w = lw.widget.layout().itemAt(0).widget()
            assert accounts_w

            if isinstance(accounts_w, LoginAccountsWidget):
                async with aqtbot.wait_signal(accounts_w.account_clicked):
                    await aqtbot.mouse_click(
                        accounts_w.accounts_widget.layout().itemAt(0).widget(), QtCore.Qt.LeftButton
                    )

            def _password_widget_shown():
                assert isinstance(lw.widget.layout().itemAt(0).widget(), LoginPasswordInputWidget)

            await aqtbot.wait_until(_password_widget_shown)

            password_w = lw.widget.layout().itemAt(0).widget()

            await aqtbot.key_clicks(password_w.line_edit_password, "P@ssw0rd")

            async with aqtbot.wait_signals([lw.login_with_password_clicked, tabw.logged_in]):
                await aqtbot.mouse_click(password_w.button_login, QtCore.Qt.LeftButton)

            central_widget = self.test_get_central_widget()
            assert central_widget is not None

            return central_widget

    return TestingMainWindow
