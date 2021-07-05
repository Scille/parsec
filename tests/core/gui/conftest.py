# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import time
from importlib import import_module
from contextlib import asynccontextmanager

import trio
import qtrio
import pytest
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


@pytest.fixture
@pytest.mark.trio
async def job_scheduler():
    async with trio.open_nursery() as nursery:
        yield QtToTrioJobScheduler(nursery)
        nursery.cancel_scope.cancel()


@pytest.fixture
def aqtbot(qtbot):
    return AsyncQtBot(qtbot)


class AsyncQtBot:
    def __init__(self, qtbot):
        self.qtbot = qtbot

    async def run(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def __getattr__(self, name):
        words = name.split("_")
        camel_name = words[0] + "".join(word.title() for word in words[1:])
        if hasattr(self.qtbot, camel_name):

            async def method(*args, **kwargs):
                return getattr(self.qtbot, camel_name)(*args, **kwargs)

            return method

        raise AttributeError(name)

    async def wait(self, timeout):
        await trio.sleep(timeout / 1000)

    async def wait_until(self, callback, *, timeout=5000):
        """Implementation shamelessly adapted from:
        https://github.com/pytest-dev/pytest-qt/blob/16b989d700dfb91fe389999d8e2676437169ed44/src/pytestqt/qtbot.py#L459
        """
        __tracebackhide__ = True

        start = time.time()

        def timed_out():
            elapsed = time.time() - start
            elapsed_ms = elapsed * 1000
            return elapsed_ms > timeout

        timeout_msg = f"wait_until timed out in {timeout} milliseconds"

        while True:
            try:
                result = callback()
            except AssertionError:
                result = False
            if result not in (None, True, False):
                msg = f"waitUntil() callback must return None, True or False, returned {result!r}"
                raise ValueError(msg)
            if result in (True, None):
                return
            if timed_out():
                raise TimeoutError(timeout_msg)
            await self.wait(10)

    @asynccontextmanager
    async def _wait_signals(self, signals):
        if not signals:
            yield
            return
        head, *tail = signals
        async with qtrio._core.wait_signal_context(head):
            async with self.wait_signals(tail):
                yield

    @asynccontextmanager
    async def wait_signals(self, signals, *, timeout=5000):
        with trio.fail_after(timeout / 1000):
            async with self._wait_signals(signals):
                yield

    @asynccontextmanager
    async def wait_signal(self, signal, *, timeout=5000):
        async with self.wait_signals((signal,), timeout=timeout):
            yield

    @asynccontextmanager
    async def wait_active(self, widget, *, timeout=5000):
        with self.qtbot.wait_active(widget, timeout=timeout):
            yield
            # TODO: make qtbot.wait_active compatible with trio
            await trio.sleep(0.2)

    @asynccontextmanager
    async def wait_exposed(self, widget, *, timeout=5000):
        with self.qtbot.wait_exposed(widget, timeout=timeout):
            yield
            # TODO: make qtbot.wait_exposed compatible with trio
            await trio.sleep(0.2)


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


@pytest.fixture(autouse=True)
def throttled_job_fast_wait(monkeypatch):
    # Throttled job slow down tests (and leads to timeout given `aqtbot.wait_until`
    # count real elapsed time and not the trio clock that can be mocked).
    # This is an autouse for all GUI tests given the throttled jobs are used
    # in really common places (e.g. files widget).
    vanilla_submit_throttled_job = QtToTrioJobScheduler.submit_throttled_job

    def _patched_submit_throttled_job(self, throttling_id, delay, *args, **kwargs):
        fast_delay = delay / 100
        return vanilla_submit_throttled_job(self, throttling_id, fast_delay, *args, **kwargs)

    monkeypatch.setattr(QtToTrioJobScheduler, "submit_throttled_job", _patched_submit_throttled_job)


@pytest.fixture
def gui_factory(
    aqtbot,
    qtbot,
    testing_main_window_cls,
    job_scheduler,
    core_config,
    monkeypatch,
    event_bus_factory,
):
    windows = []

    async def _gui_factory(
        event_bus=None,
        core_config=core_config,
        start_arg=None,
        skip_dialogs=True,
        throttle_job_no_wait=True,
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

        # Pass minimize_on_close to avoid having test blocked by the
        # closing confirmation prompt

        switch_language(core_config, "en")

        main_w = testing_main_window_cls(
            job_scheduler, event_bus, core_config, minimize_on_close=True
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
def testing_main_window_cls(aqtbot):
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

        def test_get_files_widget(self):
            mount_widget = self.test_get_mount_widget()
            return mount_widget.files_widget

        def test_get_core(self):
            return self.test_get_central_widget().core

        async def test_logout(self):
            central_widget = self.test_get_central_widget()
            tabw = self.test_get_tab()
            async with aqtbot.wait_signal(tabw.logged_out):
                central_widget.button_user.menu().actions()[2].trigger()

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
                wk_button = w_w.layout_workspaces.itemAt(i).widget()
                if isinstance(wk_button, WorkspaceButton) and wk_button.name == workspace_name:
                    break
            else:
                raise AssertionError(f"Workspace `{workspace_name}` not found")

            f_w = self.test_get_files_widget()
            async with aqtbot.wait_exposed(f_w), aqtbot.wait_signal(f_w.folder_changed):
                await aqtbot.mouse_click(wk_button, QtCore.Qt.LeftButton)

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
