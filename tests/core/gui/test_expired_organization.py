# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.datetime import DateTime
import pytest
from PyQt5 import QtCore

from parsec.backend.backend_events import BackendEvent
from parsec.core.backend_connection import (
    BackendConnectionRefused,
    backend_authenticated_cmds_factory,
)
from parsec.core.gui.login_widget import LoginPasswordInputWidget
from parsec.core.local_device import save_device_with_password
from tests.common import customize_fixtures, freeze_time


@pytest.mark.gui
@pytest.mark.trio
async def test_expired_notification_logging(
    aqtbot, running_backend, autoclose_dialog, expiredorgalice, gui_factory, core_config
):

    # Log has alice on an expired organization
    save_device_with_password(core_config.config_dir, expiredorgalice, "P@ssw0rd")

    gui = await gui_factory()
    lw = gui.test_get_login_widget()
    tabw = gui.test_get_tab()

    def _password_widget_shown():
        assert isinstance(lw.widget.layout().itemAt(0).widget(), LoginPasswordInputWidget)

    await aqtbot.wait_until(_password_widget_shown)

    password_w = lw.widget.layout().itemAt(0).widget()

    await aqtbot.key_clicks(password_w.line_edit_password, "P@ssw0rd")

    async with aqtbot.wait_signals([lw.login_with_password_clicked, tabw.logged_in]):
        await aqtbot.mouse_click(password_w.button_login, QtCore.Qt.LeftButton)

    # Assert dialog
    def _expired_notified():
        assert autoclose_dialog.dialogs == [("Error", "The organization has expired")]

    await aqtbot.wait_until(_expired_notified)


@pytest.mark.gui
@pytest.mark.trio
async def test_expired_notification_from_connection(
    aqtbot, running_backend, autoclose_dialog, expiredorgalice, gui_factory, core_config
):
    save_device_with_password(core_config.config_dir, expiredorgalice, "P@ssw0rd")
    gui = await gui_factory()
    lw = gui.test_get_login_widget()
    tabw = gui.test_get_tab()

    # Force logging on an expired organization
    with freeze_time("1989-12-17"):

        def _password_widget_shown():
            assert isinstance(lw.widget.layout().itemAt(0).widget(), LoginPasswordInputWidget)

        await aqtbot.wait_until(_password_widget_shown)

        password_w = lw.widget.layout().itemAt(0).widget()

        await aqtbot.key_clicks(password_w.line_edit_password, "P@ssw0rd")

        async with aqtbot.wait_signals([lw.login_with_password_clicked, tabw.logged_in]):
            await aqtbot.mouse_click(password_w.button_login, QtCore.Qt.LeftButton)

        # Assert logged in
        def _notified():
            assert autoclose_dialog.dialogs == []
            central_widget = gui.test_get_central_widget()
            assert central_widget is not None

        await aqtbot.wait_until(_notified)

    # Trigger another handshake
    with pytest.raises(BackendConnectionRefused):
        async with backend_authenticated_cmds_factory(
            expiredorgalice.organization_addr,
            expiredorgalice.device_id,
            expiredorgalice.signing_key,
        ) as cmds:
            async with cmds.acquire_transport():
                # This shall never happen, we shall have been rejected while acquiring the transport
                assert False

    # Assert dialog
    def _expired_notified():
        assert autoclose_dialog.dialogs == [("Error", "The organization has expired")]

    await aqtbot.wait_until(_expired_notified)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(logged_gui_as_admin=True)
async def test_expired_notification_from_update(
    aqtbot, logged_gui, running_backend, autoclose_dialog, alice
):

    # Set expiration date
    with running_backend.backend.event_bus.listen() as spy:
        await running_backend.backend.organization.set_expiration_date(
            alice.organization_id, DateTime(1989, 1, 1)
        )
        await spy.wait_with_timeout(BackendEvent.ORGANIZATION_EXPIRED)

    def _expired_notified():
        assert autoclose_dialog.dialogs == [("Error", "The organization has expired")]

    await aqtbot.wait_until(_expired_notified)
