# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from parsec.core.types.local_device import LocalDevice
from parsec.core.core_events import CoreEvent
import sys
from typing import Callable, Optional, Tuple, cast
from structlog import get_logger
from distutils.version import LooseVersion

from PyQt5.QtCore import QCoreApplication, pyqtSignal, Qt, QSize
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QResizeEvent, QCloseEvent
from PyQt5.QtWidgets import QMainWindow, QMenu, QShortcut

from parsec import __version__ as PARSEC_VERSION
from parsec.event_bus import EventBus, EventCallback
from parsec.core.local_device import list_available_devices, get_key_file
from parsec.core.config import CoreConfig, save_config
from parsec.core.types import (
    BackendActionAddr,
    BackendInvitationAddr,
    BackendOrganizationBootstrapAddr,
    BackendOrganizationFileLinkAddr,
)
from parsec.api.protocol import InvitationType
from parsec.core.gui.trio_thread import QtToTrioJobScheduler
from parsec.core.gui.lang import translate as _
from parsec.core.gui.instance_widget import InstanceWidget
from parsec.core.gui.parsec_application import ParsecApp
from parsec.core.gui import telemetry
from parsec.core.gui import desktop
from parsec.core import win_registry
from parsec.core.gui.changelog_widget import ChangelogWidget
from parsec.core.gui.claim_user_widget import ClaimUserWidget
from parsec.core.gui.claim_device_widget import ClaimDeviceWidget
from parsec.core.gui.license_widget import LicenseWidget
from parsec.core.gui.about_widget import AboutWidget
from parsec.core.gui.settings_widget import SettingsWidget
from parsec.core.gui.keys_widget import KeysWidget
from parsec.core.gui.custom_dialogs import (
    ask_question,
    show_error,
    show_info,
    GreyedDialog,
    get_text_input,
)
from parsec.core.gui.custom_widgets import Button, ensure_string_size
from parsec.core.gui.create_org_widget import CreateOrgWidget
from parsec.core.gui.ui.main_window import Ui_MainWindow
from parsec.core.gui.central_widget import (
    GoToFileLinkBadOrganizationIDError,
    GoToFileLinkBadWorkspaceIDError,
    GoToFileLinkPathDecryptionError,
)


logger = get_logger()


class MainWindow(QMainWindow, Ui_MainWindow):  # type: ignore[misc]
    foreground_needed = pyqtSignal()
    new_instance_needed = pyqtSignal(object)
    systray_notification = pyqtSignal(str, str, int)

    TAB_NOTIFICATION_COLOR = QColor(46, 146, 208)
    TAB_NOT_SELECTED_COLOR = QColor(123, 132, 163)
    TAB_SELECTED_COLOR = QColor(12, 65, 159)

    def __init__(
        self,
        jobs_ctx: QtToTrioJobScheduler,
        quit_callback: Callable[[], None],
        event_bus: EventBus,
        config: CoreConfig,
        minimize_on_close: bool = False,
        **kwargs: object,
    ):
        super().__init__(**kwargs)
        self.setupUi(self)

        self.setMenuBar(None)
        self.jobs_ctx = jobs_ctx
        self.quit_callback = quit_callback
        self.event_bus = event_bus
        self.config = config
        self.minimize_on_close = minimize_on_close
        # Explain only once that the app stays in background
        self.minimize_on_close_notif_already_send = False
        self.force_close = False
        self.need_close = False
        self.event_bus.connect(
            CoreEvent.GUI_CONFIG_CHANGED, cast(EventCallback, self.on_config_updated)
        )
        self.setWindowTitle(_("TEXT_PARSEC_WINDOW_TITLE_version").format(version=PARSEC_VERSION))
        self.foreground_needed.connect(self._on_foreground_needed)
        self.new_instance_needed.connect(self._on_new_instance_needed)
        self.tab_center.tabCloseRequested.connect(self.close_tab)

        self.menu_button = Button()
        self.menu_button.setCursor(Qt.PointingHandCursor)
        self.menu_button.setIcon(QIcon(":/icons/images/material/menu.svg"))
        self.menu_button.setIconSize(QSize(24, 24))
        self.menu_button.setText(_("ACTION_MAIN_MENU_SHOW"))
        self.menu_button.setObjectName("MenuButton")
        self.menu_button.setProperty("color", QColor(0x00, 0x92, 0xFF))
        self.menu_button.setProperty("hover_color", QColor(0x00, 0x70, 0xDD))
        self.menu_button.setStyleSheet(
            "#MenuButton {background: none; border: none; color: #0092FF;}"
            "#MenuButton:hover {color: #0070DD;}"
        )
        self.menu_button.apply_style()
        self.menu_button.clicked.connect(self._show_menu)
        self.tab_center.setCornerWidget(self.menu_button, Qt.TopRightCorner)

        self.add_tab_button = Button()
        self.add_tab_button.setCursor(Qt.PointingHandCursor)
        self.add_tab_button.setIcon(QIcon(":/icons/images/material/add.svg"))
        self.add_tab_button.setIconSize(QSize(24, 24))
        self.add_tab_button.setProperty("color", QColor(0x00, 0x92, 0xFF))
        self.add_tab_button.setProperty("hover_color", QColor(0x00, 0x70, 0xDD))
        self.add_tab_button.setStyleSheet("background: none; border: none;")
        self.add_tab_button.apply_style()
        self.add_tab_button.clicked.connect(self._on_add_instance_clicked)
        self.tab_center.setCornerWidget(self.add_tab_button, Qt.TopLeftCorner)

        self.tab_center.currentChanged.connect(self.on_current_tab_changed)
        self._define_shortcuts()
        self.ensurePolished()

    def _define_shortcuts(self) -> None:
        self.shortcut_close = QShortcut(QKeySequence(QKeySequence.Close), self)
        self.shortcut_close.activated.connect(self._shortcut_proxy(self.close_current_tab))
        self.shortcut_new_tab = QShortcut(QKeySequence(QKeySequence.AddTab), self)
        self.shortcut_new_tab.activated.connect(self._shortcut_proxy(self._on_add_instance_clicked))
        self.shortcut_settings = QShortcut(QKeySequence(_("Ctrl+K")), self)
        self.shortcut_settings.activated.connect(self._shortcut_proxy(self._show_settings))
        self.shortcut_menu = QShortcut(QKeySequence(_("Alt+E")), self)
        self.shortcut_menu.activated.connect(self._shortcut_proxy(self._show_menu))
        self.shortcut_help = QShortcut(QKeySequence(QKeySequence.HelpContents), self)
        self.shortcut_help.activated.connect(self._shortcut_proxy(self._on_show_doc_clicked))
        self.shortcut_quit = QShortcut(QKeySequence(QKeySequence.Quit), self)
        self.shortcut_quit.activated.connect(self._shortcut_proxy(self.close_app))
        self.shortcut_create_org = QShortcut(QKeySequence(QKeySequence.New), self)
        self.shortcut_create_org.activated.connect(
            self._shortcut_proxy(self._on_create_org_clicked)
        )
        self.shortcut_join_org = QShortcut(QKeySequence(QKeySequence.Open), self)
        self.shortcut_join_org.activated.connect(self._shortcut_proxy(self._on_join_org_clicked))
        shortcut = QShortcut(QKeySequence(QKeySequence.NextChild), self)
        shortcut.activated.connect(self._shortcut_proxy(self._cycle_tabs(1)))
        shortcut = QShortcut(QKeySequence(QKeySequence.PreviousChild), self)
        shortcut.activated.connect(self._shortcut_proxy(self._cycle_tabs(-1)))

    def _shortcut_proxy(self, funct: Callable[[], None]) -> Callable[[], None]:
        def _inner_proxy() -> None:
            if ParsecApp.has_active_modal():
                return
            funct()

        return _inner_proxy

    def _cycle_tabs(self, offset: int) -> Callable[[], None]:
        def _inner_cycle_tabs() -> None:
            idx = self.tab_center.currentIndex()
            idx += offset
            if idx >= self.tab_center.count():
                idx = 0
            if idx < 0:
                idx = self.tab_center.count() - 1
            self.switch_to_tab(idx)

        return _inner_cycle_tabs

    def _toggle_add_tab_button(self) -> None:
        if self._get_login_tab_index() == -1:
            self.add_tab_button.setDisabled(False)
        else:
            self.add_tab_button.setDisabled(True)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        for win in self.children():
            if win.objectName() == "GreyedDialog":
                win.resize(event.size())
                win.move(0, 0)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("MainMenu")
        action = None

        idx = self._get_login_tab_index()
        action = menu.addAction(_("ACTION_MAIN_MENU_ADD_INSTANCE"))
        action.triggered.connect(self._on_add_instance_clicked)
        action.setShortcut(self.shortcut_new_tab.key())
        action.setShortcutVisibleInContextMenu(True)

        if idx != -1:
            action.setDisabled(True)

        action = menu.addAction(_("ACTION_MAIN_MENU_CREATE_ORGANIZATION"))
        action.triggered.connect(self._on_create_org_clicked)
        action.setShortcut(self.shortcut_create_org.key())
        action.setShortcutVisibleInContextMenu(True)

        action = menu.addAction(_("ACTION_MAIN_MENU_JOIN_ORGANIZATION"))
        action.triggered.connect(self._on_join_org_clicked)
        action.setShortcut(self.shortcut_join_org.key())
        action.setShortcutVisibleInContextMenu(True)

        action = menu.addAction(_("ACTION_MAIN_MENU_MANAGE_KEYS"))
        action.triggered.connect(self._on_manage_keys)

        menu.addSeparator()

        action = menu.addAction(_("ACTION_MAIN_MENU_SETTINGS"))
        action.triggered.connect(self._show_settings)
        action.setShortcut(self.shortcut_settings.key())
        action.setShortcutVisibleInContextMenu(True)

        action = menu.addAction(_("ACTION_MAIN_MENU_OPEN_DOCUMENTATION"))
        action.triggered.connect(self._on_show_doc_clicked)
        action.setShortcut(self.shortcut_help.key())
        action.setShortcutVisibleInContextMenu(True)

        action = menu.addAction(_("ACTION_MAIN_MENU_ABOUT"))
        action.triggered.connect(self._show_about)
        action = menu.addAction(_("ACTION_MAIN_MENU_CHANGELOG"))
        action.triggered.connect(self._show_changelog)
        action = menu.addAction(_("ACTION_MAIN_MENU_LICENSE"))
        action.triggered.connect(self._show_license)
        action = menu.addAction(_("ACTION_MAIN_MENU_FEEDBACK_SEND"))
        action.triggered.connect(self._on_send_feedback_clicked)
        menu.addSeparator()
        action = menu.addAction(_("ACTION_MAIN_MENU_QUIT_PARSEC"))
        action.triggered.connect(self.close_app)
        action.setShortcut(self.shortcut_quit.key())
        action.setShortcutVisibleInContextMenu(True)

        pos = self.menu_button.pos()
        pos.setY(pos.y() + self.menu_button.size().height())
        pos = self.mapToGlobal(pos)
        menu.exec_(pos)
        menu.setParent(None)

    def _show_about(self) -> None:
        w = AboutWidget()
        d = GreyedDialog(w, title="", parent=self, width=1000)
        d.exec_()

    def _show_license(self) -> None:
        w = LicenseWidget()
        d = GreyedDialog(w, title=_("TEXT_LICENSE_TITLE"), parent=self, width=1000)
        d.exec_()

    def _show_changelog(self) -> None:
        w = ChangelogWidget()
        d = GreyedDialog(w, title=_("TEXT_CHANGELOG_TITLE"), parent=self, width=1000)
        d.exec_()

    def _show_settings(self) -> None:
        w = SettingsWidget(self.config, self.jobs_ctx, self.event_bus)
        d = GreyedDialog(w, title=_("TEXT_SETTINGS_TITLE"), parent=self, width=1000)
        d.exec_()

    def _on_manage_keys(self) -> None:
        w = KeysWidget(config=self.config, parent=self)
        w.key_imported.connect(self.reload_login_devices)
        d = GreyedDialog(w, title=_("TEXT_KEYS_DIALOG"), parent=self, width=800)
        d.exec()

    def _on_show_doc_clicked(self) -> None:
        desktop.open_doc_link()

    def _on_send_feedback_clicked(self) -> None:
        desktop.open_feedback_link()

    def _on_add_instance_clicked(self) -> None:
        self.add_instance()

    def _on_create_org_clicked(
        self, addr: Optional[BackendOrganizationBootstrapAddr] = None
    ) -> None:
        def _on_finished(ret: Optional[Tuple[LocalDevice, str]]) -> None:
            if ret is None:
                return
            self.reload_login_devices()
            device, password = ret
            self.try_login(device, password)

        CreateOrgWidget.show_modal(
            self.jobs_ctx, self.config, self, on_finished=_on_finished, start_addr=addr
        )

    def _on_join_org_clicked(self) -> None:
        url = get_text_input(
            parent=self,
            title=_("TEXT_JOIN_ORG_URL_TITLE"),
            message=_("TEXT_JOIN_ORG_URL_INSTRUCTIONS"),
            placeholder=_("TEXT_JOIN_ORG_URL_PLACEHOLDER"),
        )
        if url is None:
            return
        elif url == "":
            show_error(self, _("TEXT_JOIN_ORG_INVALID_URL"))
            return

        action_addr = None
        try:
            action_addr = BackendActionAddr.from_url(url, allow_http_redirection=True)
        except ValueError as exc:
            show_error(self, _("TEXT_INVALID_URL"), exception=exc)
            return

        if isinstance(action_addr, BackendOrganizationBootstrapAddr):
            self._on_create_org_clicked(action_addr)
        elif isinstance(action_addr, BackendInvitationAddr):
            if action_addr.invitation_type == InvitationType.USER:
                self._on_claim_user_clicked(action_addr)
            elif action_addr.invitation_type == InvitationType.DEVICE:
                self._on_claim_device_clicked(action_addr)
            else:
                show_error(self, _("TEXT_INVALID_URL"))
                return
        else:
            show_error(self, _("TEXT_INVALID_URL"))
            return

    def _on_claim_user_clicked(self, action_addr: BackendInvitationAddr) -> None:
        widget: ClaimDeviceWidget

        def _on_finished() -> None:
            nonlocal widget
            if not widget.status:
                return
            device, password = widget.status
            self.reload_login_devices()
            self.try_login(device, password)

        widget = ClaimUserWidget.show_modal(
            jobs_ctx=self.jobs_ctx,
            config=self.config,
            addr=action_addr,
            parent=self,
            on_finished=_on_finished,
        )

    def _on_claim_device_clicked(self, action_addr: BackendInvitationAddr) -> None:
        widget: ClaimDeviceWidget

        def _on_finished() -> None:
            nonlocal widget
            if not widget.status:
                return
            device, password = widget.status
            self.reload_login_devices()
            self.try_login(device, password)

        widget = ClaimDeviceWidget.show_modal(
            jobs_ctx=self.jobs_ctx,
            config=self.config,
            addr=action_addr,
            parent=self,
            on_finished=_on_finished,
        )

    def try_login(self, device: LocalDevice, password: str) -> None:
        idx = self._get_login_tab_index()
        if idx == -1:
            tab = self.add_new_tab()
        else:
            tab = self.tab_center.widget(idx)
        kf = get_key_file(self.config.config_dir, device)
        tab.login_with_password(kf, password)

    def reload_login_devices(self) -> None:
        idx = self._get_login_tab_index()
        if idx == -1:
            return
        w = self.tab_center.widget(idx)
        if not w:
            return
        w.show_login_widget()

    def on_current_tab_changed(self, index: int) -> None:
        for i in range(self.tab_center.tabBar().count()):
            if i != index:
                if self.tab_center.tabBar().tabTextColor(i) != MainWindow.TAB_NOTIFICATION_COLOR:
                    self.tab_center.tabBar().setTabTextColor(i, MainWindow.TAB_NOT_SELECTED_COLOR)
            else:
                self.tab_center.tabBar().setTabTextColor(i, MainWindow.TAB_SELECTED_COLOR)

    def _on_foreground_needed(self) -> None:
        self.show_top()

    def _on_new_instance_needed(self, start_arg: Optional[str]) -> None:
        self.add_instance(start_arg)
        self.show_top()

    def on_config_updated(self, event: CoreEvent, **kwargs: object) -> None:
        self.config = self.config.evolve(**kwargs)
        save_config(self.config)
        telemetry.init(self.config)

    def show_window(
        self, skip_dialogs: bool = False, invitation_link: Optional[str] = None
    ) -> None:
        try:
            if not self.restoreGeometry(self.config.gui_geometry):
                self.showMaximized()
        except TypeError:
            self.showMaximized()

        QCoreApplication.processEvents()

        # Used with the --diagnose option
        if skip_dialogs:
            return

        # At the very first launch
        if self.config.gui_first_launch:
            r = ask_question(
                self,
                _("TEXT_ENABLE_TELEMETRY_TITLE"),
                _("TEXT_ENABLE_TELEMETRY_INSTRUCTIONS"),
                [_("ACTION_ENABLE_TELEMETRY_ACCEPT"), _("ACTION_ENABLE_TELEMETRY_REFUSE")],
                oriented_question=True,
            )

            # Acknowledge the changes
            self.event_bus.send(
                CoreEvent.GUI_CONFIG_CHANGED,
                gui_first_launch=False,
                gui_last_version=PARSEC_VERSION,
                telemetry_enabled=r == _("ACTION_ENABLE_TELEMETRY_ACCEPT"),
            )

        # For each parsec update
        if self.config.gui_last_version and self.config.gui_last_version != PARSEC_VERSION:

            # Update from parsec `<1.14` to `>=1.14`
            if LooseVersion(self.config.gui_last_version) < "1.14":

                # Revert the acrobat reader workaround
                if (
                    sys.platform == "win32"
                    and win_registry.is_acrobat_reader_dc_present()
                    and not win_registry.get_acrobat_app_container_enabled()
                ):
                    win_registry.del_acrobat_app_container_enabled()

            # Acknowledge the changes
            self.event_bus.send(CoreEvent.GUI_CONFIG_CHANGED, gui_last_version=PARSEC_VERSION)

        telemetry.init(self.config)

        devices = list_available_devices(self.config.config_dir)
        if not len(devices) and not invitation_link:
            r = ask_question(
                self,
                _("TEXT_KICKSTART_PARSEC_WHAT_TO_DO_TITLE"),
                _("TEXT_KICKSTART_PARSEC_WHAT_TO_DO_INSTRUCTIONS"),
                [
                    _("ACTION_NO_DEVICE_CREATE_ORGANIZATION"),
                    _("ACTION_NO_DEVICE_JOIN_ORGANIZATION"),
                ],
                radio_mode=True,
            )
            if r == _("ACTION_NO_DEVICE_JOIN_ORGANIZATION"):
                self._on_join_org_clicked()
            elif r == _("ACTION_NO_DEVICE_CREATE_ORGANIZATION"):
                self._on_create_org_clicked()

    def show_top(self) -> None:
        self.activateWindow()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.show()

    def on_tab_state_changed(self, tab: InstanceWidget, state: str) -> None:
        idx = self.tab_center.indexOf(tab)
        if idx == -1:
            return
        if state == "login":
            if self._get_login_tab_index() != -1:
                self.tab_center.removeTab(idx)
            else:
                self.tab_center.setTabToolTip(idx, _("TEXT_TAB_TITLE_LOG_IN_SCREEN"))
                self.tab_center.setTabText(idx, _("TEXT_TAB_TITLE_LOG_IN_SCREEN"))
        elif state == "logout":
            self.tab_center.removeTab(idx)
            idx = self._get_login_tab_index()
            if idx == -1:
                self.add_instance()
            else:
                tab_widget = self.tab_center.widget(idx)
                log_widget = None if not tab_widget else tab_widget.get_login_widget()
                if log_widget:
                    log_widget.reload_devices()
        elif state == "connected":
            device = tab.current_device
            tab_name = (
                f"{device.organization_id} - {device.short_user_display} - {device.device_display}"
            )
            self.tab_center.setTabToolTip(idx, tab_name)
            self.tab_center.setTabText(
                idx, ensure_string_size(tab_name, 150, self.tab_center.tabBar().font())
            )
        if self.tab_center.count() == 1:
            self.tab_center.setTabsClosable(False)
        self._toggle_add_tab_button()

    def on_tab_notification(self, tab: InstanceWidget, event: CoreEvent) -> None:
        idx = self.tab_center.indexOf(tab)
        if idx == -1 or idx == self.tab_center.currentIndex():
            return
        if event == CoreEvent.SHARING_UPDATED:
            self.tab_center.tabBar().setTabTextColor(idx, MainWindow.TAB_NOTIFICATION_COLOR)

    def _get_login_tab_index(self) -> int:
        for idx in range(self.tab_center.count()):
            if self.tab_center.tabText(idx) == _("TEXT_TAB_TITLE_LOG_IN_SCREEN"):
                return idx
        return -1

    def add_new_tab(self) -> InstanceWidget:
        tab = InstanceWidget(self.jobs_ctx, self.event_bus, self.config, self.systray_notification)
        tab.join_organization_clicked.connect(self._on_join_org_clicked)
        tab.create_organization_clicked.connect(self._on_create_org_clicked)
        idx = self.tab_center.addTab(tab, "")
        tab.state_changed.connect(self.on_tab_state_changed)
        self.tab_center.setCurrentIndex(idx)
        if self.tab_center.count() > 1:
            self.tab_center.setTabsClosable(True)
        else:
            self.tab_center.setTabsClosable(False)
        return tab

    def switch_to_tab(self, idx: int) -> None:
        if not ParsecApp.has_active_modal():
            self.tab_center.setCurrentIndex(idx)

    def switch_to_login_tab(
        self, file_link_addr: Optional[BackendOrganizationFileLinkAddr] = None
    ) -> None:
        # Retrieve the login tab
        idx = self._get_login_tab_index()
        if idx != -1:
            self.switch_to_tab(idx)
        else:
            # No loging tab, create one
            tab = self.add_new_tab()
            tab.show_login_widget()
            self.on_tab_state_changed(tab, "login")
            idx = self.tab_center.count() - 1
            self.switch_to_tab(idx)

        if not file_link_addr:
            # We're done here
            return

        # Find the device corresponding to the organization in the link
        for available_device in list_available_devices(self.config.config_dir):
            if available_device.organization_id == file_link_addr.organization_id:
                break

        else:
            # Cannot reach this organization with our available devices
            show_error(
                self,
                _("TEXT_FILE_LINK_NOT_IN_ORG_organization").format(
                    organization=file_link_addr.organization_id
                ),
            )
            return

        # Pre-select the corresponding device
        login_w = self.tab_center.widget(idx).get_login_widget()
        login_w._on_account_clicked(available_device)

        # Set the path
        instance_widget = self.tab_center.widget(idx)
        instance_widget.set_workspace_path(file_link_addr)

        # Prompt the user for the need to log in first
        show_info(
            self,
            _("TEXT_FILE_LINK_PLEASE_LOG_IN_organization").format(
                organization=file_link_addr.organization_id
            ),
        )

    def go_to_file_link(self, addr: BackendOrganizationFileLinkAddr) -> None:
        # Try to use the file link on the already logged in cores
        for idx in range(self.tab_center.count()):
            if self.tab_center.tabText(idx) == _("TEXT_TAB_TITLE_LOG_IN_SCREEN"):
                continue

            w = self.tab_center.widget(idx)
            if (
                not w
                or not w.core
                or w.core.device.organization_addr.organization_id != addr.organization_id
            ):
                continue

            central_widget = w.get_central_widget()
            if not central_widget:
                continue

            try:
                central_widget.go_to_file_link(addr)

            except GoToFileLinkBadOrganizationIDError:
                continue
            except GoToFileLinkBadWorkspaceIDError:
                # Switch tab so user understand where the error comes from
                self.switch_to_tab(idx)
                show_error(
                    self,
                    _("TEXT_FILE_LINK_WORKSPACE_NOT_FOUND_organization").format(
                        organization=addr.organization_id
                    ),
                )
                return
            except GoToFileLinkPathDecryptionError:
                # Switch tab so user understand where the error comes from
                self.switch_to_tab(idx)
                show_error(self, _("TEXT_INVALID_URL"))
                return
            else:
                self.switch_to_tab(idx)
                return

        # The file link is from an organization we'r not currently logged in
        # or we don't have any device related to
        self.switch_to_login_tab(addr)

    def show_create_org_widget(self, action_addr: BackendOrganizationBootstrapAddr) -> None:
        self.switch_to_login_tab()
        self._on_create_org_clicked(action_addr)

    def show_claim_user_widget(self, action_addr: BackendInvitationAddr) -> None:
        self.switch_to_login_tab()
        self._on_claim_user_clicked(action_addr)

    def show_claim_device_widget(self, action_addr: BackendInvitationAddr) -> None:
        self.switch_to_login_tab()
        self._on_claim_device_clicked(action_addr)

    def add_instance(self, start_arg: Optional[str] = None) -> None:
        action_addr = None
        if start_arg:
            try:
                action_addr = BackendActionAddr.from_url(start_arg, allow_http_redirection=True)
            except ValueError as exc:
                show_error(self, _("TEXT_INVALID_URL"), exception=exc)

        self.show_top()
        if not action_addr:
            self.switch_to_login_tab()
        elif isinstance(action_addr, BackendOrganizationFileLinkAddr):
            self.go_to_file_link(action_addr)
        elif isinstance(action_addr, BackendOrganizationBootstrapAddr):
            self.show_create_org_widget(action_addr)
        elif (
            isinstance(action_addr, BackendInvitationAddr)
            and action_addr.invitation_type == InvitationType.USER
        ):
            self.show_claim_user_widget(action_addr)
        elif (
            isinstance(action_addr, BackendInvitationAddr)
            and action_addr.invitation_type == InvitationType.DEVICE
        ):
            self.show_claim_device_widget(action_addr)
        else:
            show_error(self, _("TEXT_INVALID_URL"))

    def close_current_tab(self, force: bool = False) -> None:
        if self.tab_center.count() == 1:
            self.close_app()
        else:
            idx = self.tab_center.currentIndex()
            self.close_tab(idx, force=force)

    def close_app(self, force: bool = False) -> None:
        self.show_top()
        self.need_close = True
        self.force_close = force
        self.close()

    def close_all_tabs(self) -> None:
        for idx in range(self.tab_center.count()):
            self.close_tab(idx, force=True)

    def close_tab(self, index: int, force: bool = False) -> None:
        tab = self.tab_center.widget(index)
        if not force:
            r = _("ACTION_TAB_CLOSE_CONFIRM")
            if tab and tab.is_logged_in:
                r = ask_question(
                    self,
                    _("TEXT_TAB_CLOSE_TITLE"),
                    _("TEXT_TAB_CLOSE_INSTRUCTIONS_device").format(
                        device=f"{tab.core.device.short_user_display} - {tab.core.device.device_display}"
                    ),
                    [_("ACTION_TAB_CLOSE_CONFIRM"), _("ACTION_CANCEL")],
                )
            if r != _("ACTION_TAB_CLOSE_CONFIRM"):
                return
        self.tab_center.removeTab(index)
        if not tab:
            return
        tab.logout()
        self.reload_login_devices()
        if self.tab_center.count() == 1:
            self.tab_center.setTabsClosable(False)
        self._toggle_add_tab_button()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.minimize_on_close and not self.need_close:
            self.hide()
            event.ignore()

            # This notification is disabled on Mac since minimizing apps on
            # close is the standard behaviour on this OS.
            if not self.minimize_on_close_notif_already_send and sys.platform != "darwin":
                self.minimize_on_close_notif_already_send = True
                self.systray_notification.emit(
                    "Parsec", _("TEXT_TRAY_PARSEC_STILL_RUNNING_MESSAGE"), 2000
                )
        else:
            if self.config.gui_confirmation_before_close and not self.force_close:
                result = ask_question(
                    self if self.isVisible() else None,
                    _("TEXT_PARSEC_QUIT_TITLE"),
                    _("TEXT_PARSEC_QUIT_INSTRUCTIONS"),
                    [_("ACTION_PARSEC_QUIT_CONFIRM"), _("ACTION_CANCEL")],
                )
                if result != _("ACTION_PARSEC_QUIT_CONFIRM"):
                    event.ignore()
                    self.force_close = False
                    self.need_close = False
                    return

            state = self.saveGeometry()
            self.event_bus.send(CoreEvent.GUI_CONFIG_CHANGED, gui_geometry=state)
            self.close_all_tabs()
            event.accept()
            self.quit_callback()
