# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from PyQt5 import QtCore

from parsec.datetime import now as datetime_now
from parsec.api.protocol import OrganizationID, HumanHandle
from parsec.core.backend_connection import apiv1_backend_anonymous_cmds_factory
from parsec.core.types import BackendOrganizationBootstrapAddr
from parsec.core.invite import bootstrap_organization
from parsec.core.gui.lang import translate

from tests.fixtures import local_device_to_backend_user
from tests.common import customize_fixtures, freeze_time


@pytest.fixture
def catch_create_org_widget(widget_catcher_factory):
    return widget_catcher_factory("parsec.core.gui.create_org_widget.CreateOrgWidget")


@pytest.fixture
async def organization_bootstrap_addr(running_backend):
    org_id = OrganizationID("AnomalousMaterials")
    org_token = "123456"
    await running_backend.backend.organization.create(org_id, org_token)
    return BackendOrganizationBootstrapAddr.build(running_backend.addr, org_id, org_token)


async def _do_creation_process(aqtbot, co_w):
    def _user_widget_ready():
        assert co_w.user_widget.isVisible()
        assert not co_w.device_widget.isVisible()
        assert not co_w.button_previous.isVisible()
        assert not co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_user_widget_ready)

    # Adding a few spaces to the name
    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_full_name, "  Gordon     Freeman   ")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_email, "gordon.freeman@blackmesa.com")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_org_name, "AnomalousMaterials")
    assert not co_w.button_validate.isEnabled()

    await aqtbot.mouse_click(co_w.user_widget.check_accept_contract, QtCore.Qt.LeftButton)

    def _user_widget_button_validate_ready():
        assert co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_user_widget_button_validate_ready)

    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _device_widget_ready():
        assert not co_w.user_widget.isVisible()
        assert co_w.device_widget.isVisible()
        assert co_w.button_previous.isVisible()
        assert not co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_device_widget_ready)

    await aqtbot.key_clicks(co_w.device_widget.line_edit_device, "HEV")
    assert co_w.device_widget.widget_password.label_password_warning.text() == translate(
        "TEXT_PASSWORD_WARNING"
    )
    await aqtbot.key_clicks(co_w.device_widget.widget_password.line_edit_password, "nihilanth")
    assert not co_w.button_validate.isEnabled()

    await aqtbot.key_clicks(
        co_w.device_widget.widget_password.line_edit_password_check, "nihilanth"
    )

    def _device_widget_button_validate_ready():
        assert co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_device_widget_button_validate_ready)
    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
async def test_create_organization(
    gui, aqtbot, running_backend, catch_create_org_widget, autoclose_dialog
):
    # The org creation window is usually opened using a sub-menu.
    # Sub-menus can be a bit challenging to open in tests so we cheat
    # using the keyboard shortcut Ctrl+N that has the same effect.
    await aqtbot.key_click(gui, "n", QtCore.Qt.ControlModifier, 200)

    co_w = await catch_create_org_widget()

    assert co_w
    await _do_creation_process(aqtbot, co_w)

    def _modal_shown():
        assert autoclose_dialog.dialogs == [
            (
                "",
                "You organization <b>AnomalousMaterials</b> has been created!<br />\n<br />\n"
                "You will now be automatically logged in.<br />\n<br />\n"
                "To help you start with PARSEC, you can read the "
                '<a href="https://docs.parsec.cloud/en/stable/" title="User guide">user guide</a>.',
            )
        ]

    await aqtbot.wait_until(_modal_shown)

    def _logged_in():
        c_w = gui.test_get_central_widget()
        assert c_w
        assert c_w.button_user.text() == "AnomalousMaterials\nGordon Freeman"

    await aqtbot.wait_until(_logged_in)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
async def test_create_organization_offline(
    gui, aqtbot, running_backend, catch_create_org_widget, autoclose_dialog
):
    with running_backend.offline():
        await aqtbot.key_click(gui, "n", QtCore.Qt.ControlModifier, 200)

        co_w = await catch_create_org_widget()
        assert co_w

        await _do_creation_process(aqtbot, co_w)

        def _modal_shown():
            assert autoclose_dialog.dialogs == [("Error", "Cannot connect to the server.")]

        await aqtbot.wait_until(_modal_shown)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
async def test_create_organization_same_name(
    gui,
    aqtbot,
    running_backend,
    catch_create_org_widget,
    autoclose_dialog,
    organization_bootstrap_addr,
):
    # Create an org
    human_handle = HumanHandle(email="zack@example.com", label="Zack")

    async with apiv1_backend_anonymous_cmds_factory(addr=organization_bootstrap_addr) as cmds:
        await bootstrap_organization(cmds, human_handle=human_handle, device_label="PC1")

    # Now create an org with the same name
    await aqtbot.key_click(gui, "n", QtCore.Qt.ControlModifier, 200)

    co_w = await catch_create_org_widget()
    assert co_w

    await _do_creation_process(aqtbot, co_w)

    def _modal_shown():
        assert autoclose_dialog.dialogs == [("Error", "This organization name is already used.")]

    await aqtbot.wait_until(_modal_shown)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
async def test_create_organization_previous_clicked(
    gui, aqtbot, running_backend, catch_create_org_widget, autoclose_dialog
):
    await aqtbot.key_click(gui, "n", QtCore.Qt.ControlModifier, 200)

    co_w = await catch_create_org_widget()

    assert co_w
    await aqtbot.wait_until(co_w.user_widget.isVisible)

    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_full_name, "Gordon Freeman")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_email, "gordon.freeman@blackmesa.com")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_org_name, "AnomalousMaterials")
    await aqtbot.mouse_click(co_w.user_widget.check_accept_contract, QtCore.Qt.LeftButton)
    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    await aqtbot.wait_until(co_w.device_widget.isVisible)

    await aqtbot.key_clicks(co_w.device_widget.line_edit_device, "HEV")
    await aqtbot.key_clicks(co_w.device_widget.widget_password.line_edit_password, "nihilanth")
    await aqtbot.key_clicks(
        co_w.device_widget.widget_password.line_edit_password_check, "nihilanth"
    )

    await aqtbot.mouse_click(co_w.button_previous, QtCore.Qt.LeftButton)

    def _previous_page_ready():
        assert co_w.user_widget.isVisible()
        assert not co_w.device_widget.isVisible()
        assert not co_w.button_previous.isVisible()
        assert co_w.button_validate.isEnabled()

        assert co_w.user_widget.line_edit_user_full_name.text() == "Gordon Freeman"
        assert co_w.user_widget.line_edit_user_email.text() == "gordon.freeman@blackmesa.com"
        assert co_w.user_widget.line_edit_org_name.text() == "AnomalousMaterials"
        assert co_w.user_widget.check_accept_contract.isChecked()

        assert co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_previous_page_ready)

    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _next_page_ready():
        assert co_w.device_widget.isVisible()
        assert co_w.device_widget.line_edit_device.text() == "HEV"
        assert co_w.device_widget.widget_password.line_edit_password.text() == "nihilanth"
        assert co_w.device_widget.widget_password.line_edit_password_check.text() == "nihilanth"

    await aqtbot.wait_until(_next_page_ready)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
async def test_create_organization_bootstrap_only(
    aqtbot,
    running_backend,
    qt_thread_gateway,
    catch_create_org_widget,
    autoclose_dialog,
    gui_factory,
    organization_bootstrap_addr,
    monkeypatch,
):

    await gui_factory(start_arg=organization_bootstrap_addr.to_url())

    co_w = await catch_create_org_widget()

    assert co_w

    assert co_w.label_instructions.text() == translate(
        "TEXT_BOOTSTRAP_ORGANIZATION_INSTRUCTIONS_organization"
    ).format(organization="AnomalousMaterials")

    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_full_name, "Gordon Freeman")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_email, "gordon.freeman@blackmesa.com")

    def _user_widget_button_validate_ready():
        assert co_w.button_validate.isEnabled()

    assert co_w.user_widget.line_edit_org_name.text() == "AnomalousMaterials"
    assert co_w.user_widget.line_edit_org_name.isReadOnly() is True
    assert not co_w.user_widget.radio_use_custom.isChecked()
    assert co_w.user_widget.radio_use_commercial.isChecked()
    assert not co_w.user_widget.radio_use_custom.isEnabled()
    await aqtbot.mouse_click(co_w.user_widget.check_accept_contract, QtCore.Qt.LeftButton)

    await aqtbot.wait_until(_user_widget_button_validate_ready)

    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _device_widget_ready():
        assert not co_w.user_widget.isVisible()
        assert co_w.device_widget.isVisible()
        assert co_w.button_previous.isVisible()
        assert not co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_device_widget_ready)

    await aqtbot.key_clicks(co_w.device_widget.line_edit_device, "HEV")
    await aqtbot.key_clicks(co_w.device_widget.widget_password.line_edit_password, "nihilanth")
    await aqtbot.key_clicks(
        co_w.device_widget.widget_password.line_edit_password_check, "nihilanth"
    )

    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _modal_shown():
        assert autoclose_dialog.dialogs == [
            (
                "",
                "You organization <b>AnomalousMaterials</b> has been created!<br />\n<br />\n"
                "You will now be automatically logged in.<br />\n<br />\n"
                "To help you start with PARSEC, you can read the "
                '<a href="https://docs.parsec.cloud/en/stable/" title="User guide">user guide</a>.',
            )
        ]

    await aqtbot.wait_until(_modal_shown)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
@customize_fixtures(fake_preferred_org_creation_backend_addr=True)
async def test_create_organization_bootstrap_only_custom_server(
    aqtbot,
    running_backend,
    qt_thread_gateway,
    catch_create_org_widget,
    autoclose_dialog,
    gui_factory,
    organization_bootstrap_addr,
    monkeypatch,
):

    await gui_factory(start_arg=organization_bootstrap_addr.to_url())

    co_w = await catch_create_org_widget()

    assert co_w

    assert co_w.label_instructions.text() == translate(
        "TEXT_BOOTSTRAP_ORGANIZATION_INSTRUCTIONS_organization"
    ).format(organization="AnomalousMaterials")

    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_full_name, "Gordon Freeman")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_email, "gordon.freeman@blackmesa.com")

    def _user_widget_ready():
        assert co_w.user_widget.line_edit_org_name.text() == "AnomalousMaterials"
        assert co_w.user_widget.line_edit_org_name.isReadOnly() is True
        assert co_w.user_widget.radio_use_custom.isChecked()
        assert not co_w.user_widget.radio_use_commercial.isChecked()
        assert not co_w.user_widget.radio_use_commercial.isEnabled()
        assert len(co_w.user_widget.line_edit_backend_addr.text())
        assert not co_w.user_widget.line_edit_backend_addr.isEnabled()

    await aqtbot.wait_until(_user_widget_ready)

    await aqtbot.mouse_click(co_w.user_widget.check_accept_contract, QtCore.Qt.LeftButton)

    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _device_widget_ready():
        assert not co_w.user_widget.isVisible()
        assert co_w.device_widget.isVisible()
        assert co_w.button_previous.isVisible()
        assert not co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_device_widget_ready)

    await aqtbot.key_clicks(co_w.device_widget.line_edit_device, "HEV")
    await aqtbot.key_clicks(co_w.device_widget.widget_password.line_edit_password, "nihilanth")
    await aqtbot.key_clicks(
        co_w.device_widget.widget_password.line_edit_password_check, "nihilanth"
    )

    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _modal_shown():
        assert autoclose_dialog.dialogs == [
            (
                "",
                "You organization <b>AnomalousMaterials</b> has been created!<br />\n<br />\n"
                "You will now be automatically logged in.<br />\n<br />\n"
                "To help you start with PARSEC, you can read the "
                '<a href="https://docs.parsec.cloud/en/stable/" title="User guide">user guide</a>.',
            )
        ]

    await aqtbot.wait_until(_modal_shown)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
async def test_create_organization_already_bootstrapped(
    aqtbot,
    running_backend,
    catch_create_org_widget,
    autoclose_dialog,
    gui,
    monkeypatch,
    organization_factory,
    local_device_factory,
    alice,
):
    org = organization_factory()
    backend_user, backend_first_device = local_device_to_backend_user(alice, org)
    bootstrap_token = "123456"
    await running_backend.backend.organization.create(org.organization_id, bootstrap_token, None)
    await running_backend.backend.organization.bootstrap(
        org.organization_id,
        backend_user,
        backend_first_device,
        bootstrap_token,
        org.root_verify_key,
    )

    org_bs_addr = BackendOrganizationBootstrapAddr.build(
        running_backend.addr, org.organization_id, bootstrap_token
    )

    monkeypatch.setattr(
        "parsec.core.gui.main_window.get_text_input", lambda *args, **kwargs: (str(org_bs_addr))
    )

    # The org bootstrap window is usually opened using a sub-menu.
    # Sub-menus can be a bit challenging to open in tests so we cheat
    # using the keyboard shortcut Ctrl+O that has the same effect.
    await aqtbot.key_click(gui, "o", QtCore.Qt.ControlModifier, 200)

    co_w = await catch_create_org_widget()
    await aqtbot.wait_until(co_w.user_widget.isVisible)

    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_full_name, "Gordon Freeman")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_email, "gordon.freeman@blackmesa.com")

    def _user_widget_ready():
        assert co_w.user_widget.line_edit_org_name.text() == org.organization_id
        assert co_w.user_widget.line_edit_org_name.isReadOnly() is True

    await aqtbot.wait_until(_user_widget_ready)

    await aqtbot.mouse_click(co_w.user_widget.check_accept_contract, QtCore.Qt.LeftButton)
    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    await aqtbot.wait_until(co_w.device_widget.isVisible)

    await aqtbot.key_clicks(co_w.device_widget.line_edit_device, "HEV")
    await aqtbot.key_clicks(co_w.device_widget.widget_password.line_edit_password, "nihilanth")
    await aqtbot.key_clicks(
        co_w.device_widget.widget_password.line_edit_password_check, "nihilanth"
    )
    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _modal_shown():
        assert autoclose_dialog.dialogs == [("Error", "This bootstrap link was already used.")]

    await aqtbot.wait_until(_modal_shown)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
@customize_fixtures(fake_preferred_org_creation_backend_addr=True)
async def test_create_organization_custom_backend(
    gui,
    aqtbot,
    running_backend,
    catch_create_org_widget,
    autoclose_dialog,
    unused_tcp_port,
    qt_thread_gateway,
):
    # The org creation window is usually opened using a sub-menu.
    # Sub-menus can be a bit challenging to open in tests so we cheat
    # using the keyboard shortcut Ctrl+N that has the same effect.
    await aqtbot.key_click(gui, "n", QtCore.Qt.ControlModifier, 200)

    co_w = await catch_create_org_widget()

    assert co_w

    def _user_widget_ready():
        assert co_w.user_widget.isVisible()
        assert not co_w.device_widget.isVisible()
        assert not co_w.button_previous.isVisible()
        assert not co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_user_widget_ready)

    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_full_name, "Gordon Freeman")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_user_email, "gordon.freeman@blackmesa.com")
    await aqtbot.key_clicks(co_w.user_widget.line_edit_org_name, "AnomalousMaterials")
    await aqtbot.mouse_click(co_w.user_widget.check_accept_contract, QtCore.Qt.LeftButton)

    def _user_widget_button_validate_ready():
        assert co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_user_widget_button_validate_ready)
    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _device_widget_ready():
        assert not co_w.user_widget.isVisible()
        assert co_w.device_widget.isVisible()
        assert co_w.button_previous.isVisible()
        assert not co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_device_widget_ready)

    await aqtbot.key_clicks(co_w.device_widget.line_edit_device, "HEV")
    await aqtbot.key_clicks(co_w.device_widget.widget_password.line_edit_password, "nihilanth")
    await aqtbot.key_clicks(
        co_w.device_widget.widget_password.line_edit_password_check, "nihilanth"
    )

    def _device_widget_button_validate_ready():
        assert co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_device_widget_button_validate_ready)

    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    # Should fail because it will use an invalid backend addr
    def _error_modal_shown():
        assert autoclose_dialog.dialogs == [("Error", "Cannot connect to the server.")]

    await aqtbot.wait_until(_error_modal_shown)

    autoclose_dialog.reset()

    # Let's go back and provide a custom address
    await aqtbot.mouse_click(co_w.button_previous, QtCore.Qt.LeftButton)

    def _user_widget_ready_again():
        assert co_w.user_widget.isVisible()
        assert not co_w.device_widget.isVisible()
        assert not co_w.button_previous.isVisible()
        assert co_w.button_validate.isEnabled()

    await aqtbot.wait_until(_user_widget_ready_again)

    def _select_radio_custom():
        co_w.user_widget.radio_use_custom.setChecked(True)

    # Clicking the radio doesn't do anything, so we cheat
    await qt_thread_gateway.send_action(_select_radio_custom)

    await aqtbot.key_clicks(co_w.user_widget.line_edit_backend_addr, running_backend.addr.to_url())
    await aqtbot.wait_until(_user_widget_button_validate_ready)

    # First click to get to the device page
    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)
    # Second click to create the org
    await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

    def _modal_shown():
        assert autoclose_dialog.dialogs == [
            (
                "",
                "You organization <b>AnomalousMaterials</b> has been created!<br />\n<br />\n"
                "You will now be automatically logged in.<br />\n<br />\n"
                "To help you start with PARSEC, you can read the "
                '<a href="https://docs.parsec.cloud/en/stable/" title="User guide">user guide</a>.',
            )
        ]

    await aqtbot.wait_until(_modal_shown)


@pytest.mark.gui
@pytest.mark.trio
@customize_fixtures(backend_spontaneous_organization_boostrap=True)
async def test_create_organization_wrong_timestamp(
    gui, aqtbot, running_backend, catch_create_org_widget, autoclose_dialog, monkeypatch
):
    await aqtbot.key_click(gui, "n", QtCore.Qt.ControlModifier, 200)
    co_w = await catch_create_org_widget()
    assert co_w

    # Patch the datetime_now() just for the organization creation in the core so we have
    # a different date than the server
    def _fake_datetime_now():
        with freeze_time("2000-01-01"):
            return datetime_now()

    monkeypatch.setattr("parsec.core.invite.organization.datetime_now", _fake_datetime_now)

    await _do_creation_process(aqtbot, co_w)

    def _error_shown():
        assert autoclose_dialog.dialogs == [
            ("Error", translate("TEXT_ORG_WIZARD_INVALID_TIMESTAMP"))
        ]

    await aqtbot.wait_until(_error_shown)


@pytest.mark.gui
@pytest.mark.trio
async def test_create_organization_with_boostrap_token(
    gui, aqtbot, running_backend, catch_create_org_widget, autoclose_dialog
):
    # Firt create the organization
    bootstrap_token = "T0k3n"
    organization_id = OrganizationID("AnomalousMaterials")
    await running_backend.backend.organization.create(
        id=organization_id, bootstrap_token=bootstrap_token
    )

    good_bootstrap_addr = BackendOrganizationBootstrapAddr.build(
        running_backend.addr, organization_id, bootstrap_token
    )
    bad_bootstrap_addr = BackendOrganizationBootstrapAddr.build(
        running_backend.addr, organization_id, "B@dT0k3n"
    )

    # Now try to use the wrong bootstrap link
    for bootstrap_addr in (bad_bootstrap_addr, good_bootstrap_addr):
        autoclose_dialog.reset()

        await aqtbot.run(gui.add_instance, bootstrap_addr.to_url())

        co_w = await catch_create_org_widget()

        assert co_w

        def _user_widget_ready():
            assert co_w.user_widget.isVisible()
            assert not co_w.device_widget.isVisible()
            assert not co_w.button_previous.isVisible()
            assert not co_w.button_validate.isEnabled()
            # Organization name and server address should be already provided
            assert co_w.user_widget.line_edit_org_name.isReadOnly()
            assert not co_w.user_widget.radio_use_custom.isEnabled()

        await aqtbot.wait_until(_user_widget_ready)

        await aqtbot.key_clicks(co_w.user_widget.line_edit_user_full_name, "Gordon Freeman")
        await aqtbot.key_clicks(
            co_w.user_widget.line_edit_user_email, "gordon.freeman@blackmesa.com"
        )
        await aqtbot.mouse_click(co_w.user_widget.check_accept_contract, QtCore.Qt.LeftButton)

        def _user_widget_button_validate_ready():
            assert co_w.button_validate.isEnabled()

        await aqtbot.wait_until(_user_widget_button_validate_ready)
        await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

        def _device_widget_ready():
            assert not co_w.user_widget.isVisible()
            assert co_w.device_widget.isVisible()
            assert co_w.button_previous.isVisible()
            assert not co_w.button_validate.isEnabled()

        await aqtbot.wait_until(_device_widget_ready)

        await aqtbot.key_clicks(co_w.device_widget.line_edit_device, "HEV")
        await aqtbot.key_clicks(co_w.device_widget.widget_password.line_edit_password, "nihilanth")
        await aqtbot.key_clicks(
            co_w.device_widget.widget_password.line_edit_password_check, "nihilanth"
        )

        def _device_widget_button_validate_ready():
            assert co_w.button_validate.isEnabled()

        await aqtbot.wait_until(_device_widget_button_validate_ready)

        await aqtbot.mouse_click(co_w.button_validate, QtCore.Qt.LeftButton)

        if bootstrap_addr is bad_bootstrap_addr:

            def _error_modal_shown():
                assert autoclose_dialog.dialogs == [("Error", "This bootstrap link is invalid.")]

            await aqtbot.wait_until(_error_modal_shown)

        else:

            def _modal_shown():
                assert autoclose_dialog.dialogs == [
                    (
                        "",
                        "You organization <b>AnomalousMaterials</b> has been created!<br />\n<br />\n"
                        "You will now be automatically logged in.<br />\n<br />\n"
                        "To help you start with PARSEC, you can read the "
                        '<a href="https://docs.parsec.cloud/en/stable/" title="User guide">user guide</a>.',
                    )
                ]

            await aqtbot.wait_until(_modal_shown)
