# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from unittest.mock import MagicMock, patch, call
from PyQt5.QtWidgets import QDialog, QPushButton

from parsec.core.gui.init_organization import (
    InitWorkspaceWidget,
    InitOrganizationWidget,
    InitUsersWidget,
)
from parsec.core.gui.lang import translate as _
from parsec.core.gui.jobs.workspace import _do_workspace_create
from parsec.core.gui.jobs.user import _do_invite_user


"""
InitOrganizationWidget
"""


@pytest.mark.gui
@pytest.mark.trio
async def test_clear_current_widget(aqtbot):
    modal = InitOrganizationWidget("jobs_ctx", "core", MagicMock())
    modal.main_layout.removeWidget = MagicMock()

    modal.current_widget = None
    modal._clear_current_widget()
    modal.main_layout.removeWidget.assert_not_called()

    current_widget = MagicMock()
    modal.current_widget = current_widget
    modal.current_widget.hide = MagicMock()
    modal.current_widget.setParent = MagicMock()
    modal._clear_current_widget()
    modal.main_layout.removeWidget.assert_called_once_with(current_widget)
    current_widget.hide.assert_called_once()
    current_widget.setParent.assert_called_once_with(None)
    assert modal.current_widget is None


@pytest.mark.gui
@pytest.mark.trio
async def test_organization_widget_on_close():
    modal = InitOrganizationWidget("jobs_ctx", "core", MagicMock())
    modal.current_widget = MagicMock()
    modal.current_widget.req_job = MagicMock()
    modal.current_widget.req_job.cancel_and_join = MagicMock()

    modal.on_close()
    modal.current_widget.req_job.cancel_and_join.assert_called_once()


@pytest.mark.gui
@pytest.mark.trio
async def test_organization_widget_on_invit_successed():
    modal = InitOrganizationWidget("jobs_ctx", "core", MagicMock())
    modal.parent = MagicMock()
    modal.parent.reset = MagicMock()

    modal.on_invit_successed()
    modal.parent.reset.assert_called_once()


@pytest.mark.gui
@pytest.mark.trio
async def test_on_workspace_created():
    modal = InitOrganizationWidget("jobs_ctx", "core", MagicMock())
    modal._clear_current_widget = MagicMock()
    modal.current_widget = None

    modal.main_layout = MagicMock()
    modal.main_layout.addWidget = MagicMock()

    modal.dialog = MagicMock()
    modal.dialog.label_title = MagicMock()
    modal.dialog.label_title.setText = MagicMock()

    modal.button_validate = MagicMock()
    modal.button_validate.setEnabled = MagicMock()
    modal.button_validate.setText = MagicMock()

    modal.parent = MagicMock()
    modal.parent.parent = MagicMock()
    modal.parent.parent.parent = MagicMock()
    modal.parent.parent.parent.menu = MagicMock()
    modal.parent.parent.parent.menu.users_clicked = MagicMock()
    emit_mock = MagicMock()
    modal.parent.parent.parent.menu.users_clicked.emit = emit_mock
    modal.parent.parent.parent.users_widget = MagicMock(return_value="fake_users_widget")

    with patch(
        "parsec.core.gui.init_organization.InitUsersWidget", autospec=True
    ) as init_users_mock:
        init_user = init_users_mock.return_value
        init_user.invit_success.connect = MagicMock()

        modal.on_workspace_created()
        modal._clear_current_widget.assert_called_once()
        init_users_mock.assert_called_once_with(modal.jobs_ctx, modal.core, modal.button_validate)
        assert modal.current_widget == init_user
        assert init_user.dialog == modal.dialog
        init_user.invit_success.connect.assert_called_once_with(modal.on_invit_successed)
        modal.main_layout.addWidget.assert_called_once_with(init_user)
        emit_mock.assert_called_once()
        modal.parent = "fake_users_widget"
        modal.dialog.label_title.setText.assert_called_once_with(
            _("TEXT_FINALIZE_ORGANIZATION_SECOND_STEP_TITLE")
        )
        modal.button_validate.setEnabled.assert_called_once_with(True)
        modal.button_validate.setText.assert_called_once_with(_("ACTION_CLOSE"))


@pytest.mark.gui
@pytest.mark.trio
async def test_exec_modal():
    init_mock = MagicMock(spec=InitOrganizationWidget)
    with patch.object(InitOrganizationWidget, "__new__") as NewMock:
        with patch(
            "parsec.core.gui.init_organization.GreyedDialog", autospec=True
        ) as GreyedDialogMock:
            NewMock.return_value = init_mock
            dialog_mock = GreyedDialogMock.return_value

            dialog_mock.exec_.return_value = QDialog.Accepted
            res = InitOrganizationWidget.exec_modal("fake_jobs_ctx", "fake_core", "fake_parent")
            NewMock.assert_called_once_with(
                InitOrganizationWidget, "fake_jobs_ctx", "fake_core", "fake_parent"
            )
            GreyedDialogMock.assert_called_once_with(
                init_mock,
                _("TEXT_FINALIZE_ORGANIZATION_FIRST_STEP_TITLE"),
                parent="fake_parent",
                width=1000,
            )
            assert init_mock.dialog == dialog_mock
            assert res == init_mock

            NewMock.reset_mock()
            GreyedDialogMock.reset_mock()

            dialog_mock.exec_.return_value = "other_response"
            res = InitOrganizationWidget.exec_modal("fake_jobs_ctx", "fake_core", "fake_parent")
            NewMock.assert_called_once_with(
                InitOrganizationWidget, "fake_jobs_ctx", "fake_core", "fake_parent"
            )
            GreyedDialogMock.assert_called_once_with(
                init_mock,
                _("TEXT_FINALIZE_ORGANIZATION_FIRST_STEP_TITLE"),
                parent="fake_parent",
                width=1000,
            )
            assert init_mock.dialog == dialog_mock
            assert res is None


"""
InitWorkspaceWidget
"""


@pytest.mark.gui
@pytest.mark.trio
async def test_on_change_name_have_to_be_call():
    with patch.object(InitWorkspaceWidget, "_on_change_name") as on_change_mock:
        init_workspace_widget = InitWorkspaceWidget("jobs_ctx", "core", MagicMock())
        name_edit = init_workspace_widget.name_edit_text

        name_edit.textChanged.emit("n")
        on_change_mock.assert_called_once_with("n")
        on_change_mock.reset_mock()

        name_edit.textChanged.emit("a")
        on_change_mock.assert_called_once_with("a")


@pytest.mark.gui
@pytest.mark.trio
async def test_toggle_button_validate():
    init_workspace_widget = InitWorkspaceWidget("jobs_ctx", "core", MagicMock())
    button = init_workspace_widget.button_validate
    name_edit = init_workspace_widget.name_edit_text
    button.setEnabled = MagicMock()

    name_edit.text = MagicMock(return_value="test")
    init_workspace_widget._on_change_name()
    button.setEnabled.assert_called_once_with(True)

    button.setEnabled.reset_mock()

    name_edit.text = MagicMock(return_value="")
    init_workspace_widget._on_change_name()
    button.setEnabled.assert_called_once_with(False)


@pytest.mark.gui
@pytest.mark.trio
async def test_button_call_create_workspace():
    with patch.object(InitWorkspaceWidget, "_create_workspace") as _create_workspace_mock:
        init_workspace_widget = InitWorkspaceWidget("jobs_ctx", "core", QPushButton())
        button = init_workspace_widget.button_validate
        button.clicked.emit(True)
        _create_workspace_mock.assert_called_once()


@pytest.mark.gui
@pytest.mark.trio
async def test_on_create_success():
    init_workspace_widget = InitWorkspaceWidget("jobs_ctx", "core", MagicMock())

    with pytest.raises(AssertionError):
        init_workspace_widget._on_create_success()

    init_workspace_widget._create_job = MagicMock()
    init_workspace_widget._create_job.is_finished = MagicMock(return_value=False)

    with pytest.raises(AssertionError):
        init_workspace_widget._on_create_success()

    init_workspace_widget._create_job.is_finished.return_value = True
    init_workspace_widget._create_job.status = "nok"
    with pytest.raises(AssertionError):
        init_workspace_widget._on_create_success()

    init_workspace_widget._create_job.status = "cancelled"
    with pytest.raises(AssertionError):
        init_workspace_widget._on_create_success()

    init_workspace_widget._create_job.status = "ok"
    init_workspace_widget._on_create_success()

    assert init_workspace_widget.create_status == "ok"
    assert init_workspace_widget._create_job is None


@pytest.mark.gui
@pytest.mark.trio
async def test_on_create_error():
    init_workspace_widget = InitWorkspaceWidget("jobs_ctx", "core", MagicMock())

    with pytest.raises(AssertionError):
        init_workspace_widget._on_create_error()

    init_workspace_widget._create_job = MagicMock()
    init_workspace_widget._create_job.is_finished = MagicMock(return_value=False)

    with pytest.raises(AssertionError):
        init_workspace_widget._on_create_error()

    init_workspace_widget._create_job.is_finished.return_value = True
    init_workspace_widget._create_job.status = "ok"
    with pytest.raises(AssertionError):
        init_workspace_widget._on_create_error()

    with patch("parsec.core.gui.init_organization.show_error") as show_error_mock:
        init_workspace_widget._create_job.status = "cancelled"
        init_workspace_widget._on_create_error()
        init_workspace_widget._create_job = None
        show_error_mock.assert_not_called()

    init_workspace_widget._create_job = MagicMock()
    init_workspace_widget._create_job.is_finished = MagicMock(return_value=True)
    init_workspace_widget._create_job.status = "nok"
    exc = MagicMock()
    init_workspace_widget._create_job.exc = exc
    with patch("parsec.core.gui.init_organization.show_error") as show_error_mock:
        with patch(
            "parsec.core.gui.init_organization.handle_create_workspace_errors"
        ) as handle_invite_errors:
            handle_invite_errors.return_value = "fake_errors"
            init_workspace_widget._on_create_error()
            init_workspace_widget._create_job = None
            show_error_mock.assert_called_once_with(
                init_workspace_widget, "fake_errors", exception=exc
            )


@pytest.mark.gui
@pytest.mark.trio
async def test_create_workspace():
    init_workspace_widget = InitWorkspaceWidget(MagicMock(), "core", MagicMock())

    init_workspace_widget.jobs_ctx.submit_job = MagicMock()
    init_workspace_widget.button_validate.setEnabled = MagicMock()
    init_workspace_widget.name_edit_text.text = MagicMock(return_value="")

    init_workspace_widget._create_workspace()
    init_workspace_widget.button_validate.setEnabled.assert_called_once_with(False)
    init_workspace_widget.jobs_ctx.submit_job.assert_not_called()

    with patch("parsec.core.gui.init_organization.ThreadSafeQtSignal") as thread_signal:
        thread_signal.side_effect = ["success", "error"]
        init_workspace_widget.button_validate.setEnabled = MagicMock()
        init_workspace_widget.name_edit_text.text = MagicMock(return_value="ws1")

        init_workspace_widget._create_workspace()
        init_workspace_widget.button_validate.setEnabled.assert_called_once_with(False)
        init_workspace_widget.jobs_ctx.submit_job.assert_called_once_with(
            "success",
            "error",
            _do_workspace_create,
            core=init_workspace_widget.core,
            workspace_name="ws1",
        )
        thread_signal_calls = [
            call(init_workspace_widget, "create_success"),
            call(init_workspace_widget, "create_error"),
        ]
        thread_signal.assert_has_calls(thread_signal_calls)


"""
InitUsersWidget
"""


@pytest.mark.gui
@pytest.mark.trio
async def test_on_change_email_have_to_be_call():
    with patch.object(InitUsersWidget, "_on_change_email") as on_change_mock:
        init_users_widget = InitUsersWidget("jobs_ctx", "core", MagicMock())
        mail_edit = init_users_widget.email_edit_text
        mail_edit.textChanged.emit("n")
        on_change_mock.assert_called_once_with("n")
        on_change_mock.reset_mock()
        mail_edit.textChanged.emit("na")
        on_change_mock.assert_called_once_with("na")


@pytest.mark.gui
@pytest.mark.trio
async def test_button_validate():
    with patch.object(InitUsersWidget, "_close") as _close_mock:
        init_users_widget = InitUsersWidget("jobs_ctx", "core", QPushButton())
        button = init_users_widget.button_validate
        button.clicked.emit()
        _close_mock.assert_called_once()


@pytest.mark.gui
@pytest.mark.trio
async def test_user_widget_on_close():
    init_users_widget = InitUsersWidget("jobs_ctx", "core", MagicMock())
    init_users_widget.dialog = MagicMock()
    init_users_widget.dialog.accept = MagicMock()
    init_users_widget._close()
    init_users_widget.dialog.accept.assert_called_once()


@pytest.mark.gui
@pytest.mark.trio
async def test_on_invit_success():
    init_users_widget = InitUsersWidget("jobs_ctx", "core", MagicMock())
    init_users_widget.button_invit.setEnabled = MagicMock()
    init_users_widget.button_validate.setEnabled = MagicMock()

    with pytest.raises(AssertionError):
        init_users_widget._on_invit_success()

    init_users_widget._invit_job = MagicMock()
    init_users_widget._invit_job.is_finished = MagicMock(return_value=False)

    with pytest.raises(AssertionError):
        init_users_widget._on_invit_success()

    init_users_widget._invit_job.is_finished.return_value = True
    init_users_widget._invit_job.status = "nok"
    with pytest.raises(AssertionError):
        init_users_widget._on_invit_success()

    init_users_widget._invit_job.status = "cancelled"
    with pytest.raises(AssertionError):
        init_users_widget._on_invit_success()

    init_users_widget._invit_job.status = "ok"
    init_users_widget._on_invit_success()

    assert init_users_widget.invit_status == "ok"
    assert init_users_widget._invit_job is None
    init_users_widget.button_invit.setEnabled.assert_called_once_with(False)
    init_users_widget.button_validate.setEnabled.assert_called_once_with(True)


@pytest.mark.gui
@pytest.mark.trio
async def test_on_invite_error():
    button_validate = MagicMock()
    button_validate.setEnabled = MagicMock()

    init_users_widget = InitUsersWidget("jobs_ctx", "core", button_validate)

    with pytest.raises(AssertionError):
        init_users_widget._on_invit_error()

    init_users_widget._invit_job = MagicMock()
    init_users_widget._invit_job.is_finished = MagicMock(return_value=False)

    with pytest.raises(AssertionError):
        init_users_widget._on_invit_error()

    init_users_widget._invit_job.is_finished.return_value = True
    init_users_widget._invit_job.status = "ok"
    with pytest.raises(AssertionError):
        init_users_widget._on_invit_error()

    init_users_widget._invit_job = MagicMock()
    init_users_widget._invit_job.is_finished = MagicMock(return_value=True)
    init_users_widget._invit_job.status = "nok"
    exc = MagicMock()
    init_users_widget._invit_job.exc = exc
    with patch("parsec.core.gui.init_organization.show_error") as show_error_mock:
        with patch(
            "parsec.core.gui.init_organization.handle_invite_errors"
        ) as handle_invite_errors:
            handle_invite_errors.return_value = "fake_errors"
            init_users_widget._on_invit_error()
            init_users_widget._create_job = None
            show_error_mock.assert_called_once_with(init_users_widget, "fake_errors", exception=exc)
            button_validate.setEnabled.assert_called_once_with(True)


@pytest.mark.gui
@pytest.mark.trio
async def test_invit_user():
    init_users_widget = InitUsersWidget(MagicMock(), "core", MagicMock())

    init_users_widget.jobs_ctx.submit_job = MagicMock()
    init_users_widget.button_invit = MagicMock()
    init_users_widget.button_invit.setEnabled = MagicMock()
    init_users_widget.button_validate.setEnabled = MagicMock()
    init_users_widget.email_edit_text.text = MagicMock(return_value="")

    init_users_widget._invit_user()
    init_users_widget.button_validate.setEnabled.assert_called_once_with(False)
    init_users_widget.button_invit.setEnabled.assert_called_once_with(False)
    init_users_widget.jobs_ctx.submit_job.assert_not_called()

    with patch("parsec.core.gui.init_organization.ThreadSafeQtSignal") as thread_signal:
        thread_signal.side_effect = ["success", "error"]
        init_users_widget.button_validate.setEnabled = MagicMock()
        init_users_widget.email_edit_text.text = MagicMock(return_value="ws1")

        init_users_widget._invit_user()
        init_users_widget.button_validate.setEnabled.assert_called_once_with(False)
        init_users_widget.jobs_ctx.submit_job.assert_called_once_with(
            "success", "error", _do_invite_user, core=init_users_widget.core, email="ws1"
        )
        thread_signal_calls = [
            call(init_users_widget, "invit_success"),
            call(init_users_widget, "invit_error"),
        ]
        thread_signal.assert_has_calls(thread_signal_calls)
