# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pytest
from unittest.mock import ANY
from PyQt5 import QtCore

from parsec.core.gui.users_widget import UserInvitationButton
from parsec.core.gui.lang import translate as _

from tests.common import customize_fixtures


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize("online", (True, False))
@customize_fixtures(logged_gui_as_admin=True)
async def test_invite_user(
    aqtbot, logged_gui, bob, running_backend, monkeypatch, autoclose_dialog, email_letterbox, online
):
    u_w = await logged_gui.test_switch_to_users_widget()

    assert u_w.layout_users.count() == 3

    monkeypatch.setattr(
        "parsec.core.gui.users_widget.get_text_input",
        lambda *args, **kwargs: "hubert.farnsworth@pe.com",
    )

    if online:
        await aqtbot.mouse_click(u_w.button_add_user, QtCore.Qt.LeftButton)

        def _new_invitation_displayed():
            assert u_w.layout_users.count() == 4
            inv_btn = u_w.layout_users.itemAt(0).widget()
            assert isinstance(inv_btn, UserInvitationButton)
            assert inv_btn.email == "hubert.farnsworth@pe.com"
            assert email_letterbox.emails == [(inv_btn.email, ANY)]
            assert autoclose_dialog.dialogs == [
                (
                    "",
                    "The invitation to join your organization was successfuly sent at : <b>hubert.farnsworth@pe.com</b>",
                )
            ]

        await aqtbot.wait_until(_new_invitation_displayed)

        autoclose_dialog.reset()

        # Also checks that an invitation already exists for this user

        monkeypatch.setattr(
            "parsec.core.gui.users_widget.get_text_input",
            lambda *args, **kwargs: bob.human_handle.email,
        )
        await aqtbot.mouse_click(u_w.button_add_user, QtCore.Qt.LeftButton)

        def _already_member():
            assert autoclose_dialog.dialogs == [
                ("Error", _("TEXT_INVITE_USER_ALREADY_MEMBER_ERROR"))
            ]

        await aqtbot.wait_until(_already_member)

    else:
        with running_backend.offline():
            await aqtbot.mouse_click(u_w.button_add_user, QtCore.Qt.LeftButton)

            def _email_send_failed():
                assert autoclose_dialog.dialogs == [
                    ("Error", "The server is offline or you have no access to the internet.")
                ]

            await aqtbot.wait_until(_email_send_failed)
            assert not email_letterbox.emails


@pytest.mark.gui
@pytest.mark.trio
async def test_invite_user_not_allowed(logged_gui, running_backend):
    u_w = await logged_gui.test_switch_to_users_widget()

    # Just make sure the button is not available
    assert not u_w.button_add_user.isVisible()


@pytest.mark.gui
@pytest.mark.trio
@pytest.mark.parametrize("online", (True, False))
@customize_fixtures(logged_gui_as_admin=True)
async def test_revoke_user(
    aqtbot, running_backend, autoclose_dialog, monkeypatch, logged_gui, online
):
    u_w = await logged_gui.test_switch_to_users_widget()

    assert u_w.layout_users.count() == 3
    bob_w = u_w.layout_users.itemAt(2).widget()
    assert bob_w.label_username.text() == "Boby McBobFace"
    assert bob_w.label_email.text() == "bob@example.com"
    assert bob_w.user_info.is_revoked is False

    monkeypatch.setattr(
        "parsec.core.gui.users_widget.ask_question",
        lambda *args, **kwargs: _("ACTION_USER_REVOCATION_CONFIRM"),
    )

    if online:
        bob_w.revoke_clicked.emit(bob_w.user_info)

        def _revocation_done():
            assert bob_w.user_info.is_revoked is True
            assert autoclose_dialog.dialogs == [
                (
                    "",
                    "The user <b>Boby McBobFace</b> has been successfully revoked. Do no forget to reencrypt the workspaces that were shared with them.",
                )
            ]

        await aqtbot.wait_until(_revocation_done)

    else:
        with running_backend.offline():
            bob_w.revoke_clicked.emit(bob_w.user_info)

            def _revocation_error():
                assert bob_w.user_info.is_revoked is False
                assert autoclose_dialog.dialogs == [
                    ("Error", "The server is offline or you have no access to the internet.")
                ]

            await aqtbot.wait_until(_revocation_error)


@pytest.mark.gui
@pytest.mark.trio
async def test_revoke_user_not_allowed(
    aqtbot, running_backend, autoclose_dialog, monkeypatch, logged_gui
):
    u_w = await logged_gui.test_switch_to_users_widget()

    assert u_w.layout_users.count() == 3
    alice_w = u_w.layout_users.itemAt(0).widget()
    assert alice_w.label_email.text() == "adam@example.com"
    assert alice_w.user_info.is_revoked is False

    # TODO: we should instead check that the menu giving access to revocation button is hidden...

    monkeypatch.setattr(
        "parsec.core.gui.users_widget.ask_question",
        lambda *args, **kwargs: _("ACTION_USER_REVOCATION_CONFIRM"),
    )

    alice_w.revoke_clicked.emit(alice_w.user_info)

    def _revocation_error():
        assert alice_w.user_info.is_revoked is False
        assert autoclose_dialog.dialogs == [("Error", "Could not revoke this user.")]

    await aqtbot.wait_until(_revocation_error)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(logged_gui_as_admin=True)
async def test_cancel_user_invitation(
    aqtbot,
    logged_gui,
    running_backend,
    backend,
    autoclose_dialog,
    monkeypatch,
    alice,
    email_letterbox,
):

    email = "i@like.coffee"

    # Patch dialogs
    monkeypatch.setattr(
        "parsec.core.gui.users_widget.get_text_input", lambda *args, **kwargs: email
    )
    monkeypatch.setattr(
        "parsec.core.gui.users_widget.ask_question",
        lambda *args, **kwargs: _("TEXT_USER_INVITE_CANCEL_INVITE_ACCEPT"),
    )
    u_w = await logged_gui.test_switch_to_users_widget()

    # Invite new user
    await aqtbot.mouse_click(u_w.button_add_user, QtCore.Qt.LeftButton)

    def _new_invitation_displayed():
        assert u_w.layout_users.count() == 4
        assert autoclose_dialog.dialogs == [
            (
                "",
                f"The invitation to join your organization was successfuly sent at : <b>{ email }</b>",
            )
        ]

    await aqtbot.wait_until(_new_invitation_displayed)
    autoclose_dialog.reset()
    user_invitation_w = u_w.layout_users.itemAt(0).widget()
    assert user_invitation_w.email == email

    # Cancel invitation
    await aqtbot.mouse_click(user_invitation_w.button_cancel, QtCore.Qt.LeftButton)

    def _new_invitation_removed():
        assert u_w.layout_users.count() == 3

    await aqtbot.wait_until(_new_invitation_removed)
    assert not autoclose_dialog.dialogs
