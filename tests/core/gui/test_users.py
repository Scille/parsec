# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from PyQt5 import QtCore

from parsec.core.gui.lang import translate as _
from parsec.core.local_device import save_device_with_password


@pytest.fixture
async def logged_gui(
    aqtbot, gui_factory, running_backend, autoclose_dialog, core_config, alice, bob
):
    save_device_with_password(core_config.config_dir, alice, "P@ssw0rd")

    gui = await gui_factory()
    lw = gui.test_get_login_widget()
    tabw = gui.test_get_tab()

    await aqtbot.key_clicks(lw.line_edit_password, "P@ssw0rd")

    async with aqtbot.wait_signals([lw.login_with_password_clicked, tabw.logged_in]):
        await aqtbot.mouse_click(lw.button_login, QtCore.Qt.LeftButton)

    central_widget = gui.test_get_central_widget()
    assert central_widget is not None

    save_device_with_password(core_config.config_dir, bob, "P@ssw0rd")
    await aqtbot.mouse_click(central_widget.menu.button_users, QtCore.Qt.LeftButton)

    yield gui


@pytest.mark.gui
@pytest.mark.trio
async def test_list_users(aqtbot, running_backend, logged_gui):
    u_w = logged_gui.test_get_users_widget()
    assert u_w is not None

    async with aqtbot.wait_signal(u_w.list_success):
        pass

    assert u_w.layout_users.count() == 3
    item = u_w.layout_users.itemAt(0)
    assert item.widget().label_username.text() == "Adamy McAdamFace <adam@example.com>"
    assert item.widget().label_role.text() == "Administrator"
    assert item.widget().label_revoked.text() == ""
    item = u_w.layout_users.itemAt(1)
    assert item.widget().label_username.text() == "Alicey McAliceFace <alice@example.com>"
    assert item.widget().label_user_is_current.text() == "(you)"
    assert item.widget().label_role.text() == "Administrator"
    assert item.widget().label_revoked.text() == ""
    item = u_w.layout_users.itemAt(2)
    assert item.widget().label_username.text() == "Boby McBobFace <bob@example.com>"
    assert item.widget().label_role.text() == "Contributor"
    assert item.widget().label_revoked.text() == ""


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize("online", (True, False))
async def test_revoke_user(
    aqtbot, running_backend, autoclose_dialog, monkeypatch, logged_gui, alice, online
):
    u_w = logged_gui.test_get_users_widget()
    assert u_w is not None

    async with aqtbot.wait_signal(u_w.list_success):
        pass

    assert u_w.layout_users.count() == 3
    bob_w = u_w.layout_users.itemAt(2).widget()
    assert bob_w.user_display == "Boby McBobFace <bob@example.com>"
    assert bob_w.user_id == "bob"
    assert bob_w.is_revoked is False

    monkeypatch.setattr(
        "parsec.core.gui.users_widget.ask_question",
        lambda *args: _("ACTION_USER_REVOCATION_CONFIRM"),
    )

    if online:
        async with aqtbot.wait_signal(u_w.revoke_success):
            bob_w.revoke_clicked.emit(bob_w)
        assert len(autoclose_dialog.dialogs) == 1
        assert autoclose_dialog.dialogs[0][0] == ""
        assert (
            autoclose_dialog.dialogs[0][1]
            == "The user <b>Boby McBobFace <bob@example.com></b> has been successfully revoked. Do no forget to reencrypt the workspaces that were shared with them."
        )
        assert bob_w.is_revoked is True
    else:
        with running_backend.offline():
            async with aqtbot.wait_signal(u_w.revoke_error):
                bob_w.revoke_clicked.emit(bob_w)
            assert len(autoclose_dialog.dialogs) == 1
            assert autoclose_dialog.dialogs[0][0] == "Error"
            assert (
                autoclose_dialog.dialogs[0][1]
                == "The server is offline or you have no access to the internet."
            )
            assert bob_w.is_revoked is False


# @pytest.mark.gui
# @pytest.mark.trio
# async def test_filter_users(aqtbot, running_backend, logged_gui):
#     u_w = logged_gui.test_get_users_widget()
#     assert u_w is not None

#     async with aqtbot.wait_signal(u_w.list_success):
#         pass

#     assert u_w.layout_users.count() == 3

#     adam_w = u_w.layout_users.itemAt(0).widget()
#     assert adam_w.user_name == "Adamy McAdamFace <adam@example.com>"
#     alice_w = u_w.layout_users.itemAt(1).widget()
#     assert alice_w.user_name == "Alicey McAliceFace <alice@example.com>"
#     bob_w = u_w.layout_users.itemAt(2).widget()
#     assert bob_w.user_name == "Boby McBobFace <bob@example.com>"

#     assert alice_w.isVisible() is True
#     assert bob_w.isVisible() is True
#     assert adam_w.isVisible() is True

#     async with aqtbot.wait_signal(u_w.filter_timer.timeout):
#         aqtbot.qtbot.keyClicks(u_w.line_edit_search, "bo")
#     assert alice_w.isVisible() is False
#     assert bob_w.isVisible() is True
#     assert adam_w.isVisible() is False

# async with aqtbot.wait_signal(u_w.filter_timer.timeout):
#     u_w.line_edit_search.setText("")
# assert alice_w.isVisible() is True
# assert bob_w.isVisible() is True
# assert adam_w.isVisible() is True

# async with aqtbot.wait_signal(u_w.filter_timer.timeout):
#     aqtbot.qtbot.keyClicks(u_w.line_edit_search, "a")
# assert alice_w.isVisible() is True
# assert bob_w.isVisible() is False
# assert adam_w.isVisible() is True
