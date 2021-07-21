# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pytest
from parsec.core.gui.lang import translate


@pytest.mark.gui
@pytest.mark.trio
async def test_offline_notification(aqtbot, running_backend, logged_gui):
    central_widget = logged_gui.test_get_central_widget()
    assert central_widget is not None

    # Assert connected
    def _online():
        assert central_widget.menu.label_connection_state.text() == translate(
            "TEXT_BACKEND_STATE_CONNECTED"
        )

    await aqtbot.wait_until(_online)

    # Assert offline
    def _offline():
        central_widget.menu.label_connection_state.text() == translate(
            "TEXT_BACKEND_STATE_DISCONNECTED"
        )

    async with aqtbot.wait_signal(central_widget.systray_notification):
        with running_backend.offline():
            await aqtbot.wait_until(_offline)
