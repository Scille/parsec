# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import platform
from functools import partial
from uuid import UUID

import click

from parsec.api.data import UserProfile
from parsec.api.protocol import (
    HumanHandle,
    InvitationDeletedReason,
    InvitationStatus,
    InvitationType,
)
from parsec.cli_utils import aprompt, cli_exception_handler, operation, spinner
from parsec.core.backend_connection import (
    BackendConnectionRefused,
    backend_authenticated_cmds_factory,
    backend_invited_cmds_factory,
)
from parsec.core.cli.utils import core_config_and_device_options, core_config_options
from parsec.core.invite import (
    DeviceClaimInitialCtx,
    DeviceGreetInitialCtx,
    InviteError,
    UserClaimInitialCtx,
    UserGreetInitialCtx,
    claimer_retrieve_info,
)
from parsec.core.local_device import save_device_with_password
from parsec.core.types import BackendInvitationAddr
from parsec.utils import trio_run


async def _invite_device(config, device):
    async with spinner("Creating device invitation"):
        async with backend_authenticated_cmds_factory(
            addr=device.organization_addr,
            device_id=device.device_id,
            signing_key=device.signing_key,
            keepalive=config.backend_connection_keepalive,
        ) as cmds:
            rep = await cmds.invite_new(type=InvitationType.DEVICE)
            if rep["status"] != "ok":
                raise RuntimeError(f"Backend refused to create device invitation: {rep}")

    action_addr = BackendInvitationAddr.build(
        backend_addr=device.organization_addr,
        organization_id=device.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=rep["token"],
    )
    action_addr_display = click.style(action_addr.to_url(), fg="yellow")
    click.echo(f"url: {action_addr_display}")


@click.command(short_help="create device invitation")
@core_config_and_device_options
def invite_device(config, device, **kwargs):
    """
    Create new device invitation
    """
    with cli_exception_handler(config.debug):
        trio_run(_invite_device, config, device)


async def _invite_user(config, device, email, send_email):
    async with spinner("Creating user invitation"):
        async with backend_authenticated_cmds_factory(
            addr=device.organization_addr,
            device_id=device.device_id,
            signing_key=device.signing_key,
            keepalive=config.backend_connection_keepalive,
        ) as cmds:
            rep = await cmds.invite_new(
                type=InvitationType.USER, claimer_email=email, send_email=send_email
            )
            if rep["status"] != "ok":
                raise RuntimeError(f"Backend refused to create user invitation: {rep}")

    action_addr = BackendInvitationAddr.build(
        backend_addr=device.organization_addr,
        organization_id=device.organization_id,
        invitation_type=InvitationType.USER,
        token=rep["token"],
    )
    action_addr_display = click.style(action_addr.to_url(), fg="yellow")
    click.echo(f"url: {action_addr_display}")


@click.command(short_help="create user invitation")
@core_config_and_device_options
@click.argument("email")
@click.option("--send-email", is_flag=True)
def invite_user(config, device, email, send_email, **kwargs):
    """
    Create new user invitation
    """
    with cli_exception_handler(config.debug):
        trio_run(_invite_user, config, device, email, send_email)


async def _do_greet_user(device, initial_ctx):
    async with spinner("Waiting for claimer"):
        in_progress_ctx = await initial_ctx.do_wait_peer()

    display_greeter_sas = click.style(str(in_progress_ctx.greeter_sas), fg="yellow")
    click.echo(f"Code to provide to claimer: {display_greeter_sas}")
    async with spinner("Waiting for claimer"):
        in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()

    choices = in_progress_ctx.generate_claimer_sas_choices(size=3)
    for i, choice in enumerate(choices):
        display_choice = click.style(choice, fg="yellow")
        click.echo(f" {i} - {display_choice}")
    code = await aprompt(
        f"Select code provided by claimer",
        type=click.Choice([str(i) for i, _ in enumerate(choices)]),
    )
    if choices[int(code)] != in_progress_ctx.claimer_sas:
        click.secho("Wrong code provided", fg="red")
        return False

    async with spinner("Waiting for claimer"):
        in_progress_ctx = await in_progress_ctx.do_signify_trust()
        in_progress_ctx = await in_progress_ctx.do_get_claim_requests()

    granted_label = await aprompt(
        "New user label", default=in_progress_ctx.requested_human_handle.label
    )
    granted_email = await aprompt(
        "New user email", default=in_progress_ctx.requested_human_handle.email
    )
    granted_device_label = await aprompt(
        "New user device label", default=in_progress_ctx.requested_device_label
    )
    choices = list(UserProfile)
    for i, choice in enumerate(UserProfile):
        display_choice = click.style(choice.value, fg="yellow")
        click.echo(f" {i} - {display_choice}")
    choice_index = await aprompt(
        "New user profile", default="0", type=click.Choice([str(i) for i, _ in enumerate(choices)])
    )
    granted_profile = choices[int(choice_index)]
    async with spinner("Creating the user in the backend"):
        await in_progress_ctx.do_create_new_user(
            author=device,
            device_label=granted_device_label,
            human_handle=HumanHandle(email=granted_email, label=granted_label),
            profile=granted_profile,
        )

    return True


async def _do_greet_device(device, initial_ctx):
    async with spinner("Waiting for claimer"):
        in_progress_ctx = await initial_ctx.do_wait_peer()

    display_greeter_sas = click.style(str(in_progress_ctx.greeter_sas), fg="yellow")
    click.echo(f"Code to provide to claimer: {display_greeter_sas}")
    async with spinner("Waiting for claimer"):
        in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()

    choices = in_progress_ctx.generate_claimer_sas_choices(size=3)
    for i, choice in enumerate(choices):
        display_choice = click.style(choice, fg="yellow")
        click.echo(f" {i} - {display_choice}")
    code = await aprompt(
        f"Select code provided by claimer", type=click.Choice([str(x) for x in range(len(choices))])
    )
    if choices[int(code)] != in_progress_ctx.claimer_sas:
        click.secho("Wrong code provided", fg="red")
        return False

    async with spinner("Waiting for claimer"):
        in_progress_ctx = await in_progress_ctx.do_signify_trust()
        in_progress_ctx = await in_progress_ctx.do_get_claim_requests()

    granted_device_label = await aprompt(
        "New device label", default=in_progress_ctx.requested_device_label
    )
    async with spinner("Creating the device in the backend"):
        await in_progress_ctx.do_create_new_device(author=device, device_label=granted_device_label)

    return True


async def _greet_invitation(config, device, token):
    async with backend_authenticated_cmds_factory(
        addr=device.organization_addr,
        device_id=device.device_id,
        signing_key=device.signing_key,
        keepalive=config.backend_connection_keepalive,
    ) as cmds:
        async with spinner("Retrieving invitation info"):
            rep = await cmds.invite_list()
            if rep["status"] != "ok":
                raise RuntimeError(f"Backend error: {rep}")
        for invitation in rep["invitations"]:
            if invitation["token"] == token:
                break
        else:
            raise RuntimeError(f"Invitation not found")

        if invitation["type"] == InvitationType.USER:
            initial_ctx = UserGreetInitialCtx(cmds=cmds, token=token)
            do_greet = partial(_do_greet_user, device, initial_ctx)
        else:
            assert invitation["type"] == InvitationType.DEVICE
            initial_ctx = DeviceGreetInitialCtx(cmds=cmds, token=token)
            do_greet = partial(_do_greet_device, device, initial_ctx)

        while True:
            try:
                greet_done = await do_greet()
                if greet_done:
                    break
            except InviteError as exc:
                click.secho(str(exc), fg="red")
            click.secho("Restarting the invitation process", fg="red")


@click.command(short_help="greet invitation")
@core_config_and_device_options
@click.argument("token", type=UUID)
def greet_invitation(config, device, token, **kwargs):
    """
    Greet a new device or user into the organization
    """
    with cli_exception_handler(config.debug):
        # Disable task monitoring given user prompt will block the coroutine
        trio_run(_greet_invitation, config, device, token)


async def _do_claim_user(initial_ctx):
    async with spinner("Initializing connection with greeter for claiming user"):
        in_progress_ctx = await initial_ctx.do_wait_peer()

    choices = in_progress_ctx.generate_greeter_sas_choices(size=3)
    for i, choice in enumerate(choices):
        display_choice = click.style(choice, fg="yellow")
        click.echo(f" {i} - {display_choice}")
    code = await aprompt(
        f"Select code provided by greeter", type=click.Choice([str(x) for x in range(len(choices))])
    )
    if choices[int(code)] != in_progress_ctx.greeter_sas:
        click.secho("Wrong code provided", fg="red")
        return None

    in_progress_ctx = await in_progress_ctx.do_signify_trust()
    display_claimer_sas = click.style(str(in_progress_ctx.claimer_sas), fg="yellow")
    click.echo(f"Code to provide to greeter: {display_claimer_sas}")
    async with spinner("Waiting for greeter"):
        in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()

    requested_label = await aprompt("User fullname")
    requested_email = initial_ctx.claimer_email
    requested_device_label = await aprompt("Device label", default=platform.node())
    async with spinner("Waiting for greeter (finalizing)"):
        new_device = await in_progress_ctx.do_claim_user(
            requested_device_label=requested_device_label,
            requested_human_handle=HumanHandle(email=requested_email, label=requested_label),
        )

    return new_device


async def _do_claim_device(initial_ctx):
    async with spinner("Initializing connection with greeter for claiming device"):
        in_progress_ctx = await initial_ctx.do_wait_peer()

    choices = in_progress_ctx.generate_greeter_sas_choices(size=3)
    for i, choice in enumerate(choices):
        display_choice = click.style(choice, fg="yellow")
        click.echo(f" {i} - {display_choice}")
    code = await aprompt(
        f"Select code provided by greeter", type=click.Choice([str(x) for x in range(len(choices))])
    )
    if choices[int(code)] != in_progress_ctx.greeter_sas:
        click.secho("Wrong code provided", fg="red")
        return None

    in_progress_ctx = await in_progress_ctx.do_signify_trust()
    display_claimer_sas = click.style(str(in_progress_ctx.claimer_sas), fg="yellow")
    click.echo(f"Code to provide to greeter: {display_claimer_sas}")
    async with spinner("Waiting for greeter"):
        in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()

    requested_device_label = await aprompt("Device label", default=platform.node())
    async with spinner("Waiting for greeter (finalizing)"):
        new_device = await in_progress_ctx.do_claim_device(
            requested_device_label=requested_device_label
        )

    return new_device


async def _claim_invitation(config, addr, password):
    async with backend_invited_cmds_factory(addr=addr) as cmds:
        try:
            async with spinner("Retrieving invitation info"):
                initial_ctx = await claimer_retrieve_info(cmds)
        except BackendConnectionRefused:
            raise RuntimeError("Invitation not found")

        if initial_ctx.greeter_human_handle:
            display_greeter = click.style(str(initial_ctx.greeter_human_handle), fg="yellow")
        else:
            display_greeter = click.style(initial_ctx.greeter_user_id, fg="yellow")
        click.echo(f"Invitation greeter: {display_greeter}")
        while True:
            try:
                if isinstance(initial_ctx, DeviceClaimInitialCtx):
                    new_device = await _do_claim_device(initial_ctx)
                else:
                    assert isinstance(initial_ctx, UserClaimInitialCtx)
                    new_device = await _do_claim_user(initial_ctx)
                if new_device:
                    break
            except InviteError as exc:
                click.secho(str(exc), fg="red")
            click.secho("Restarting the invitation process", fg="red")

        device_display = click.style(new_device.slughash, fg="yellow")
        with operation(f"Saving device {device_display}"):
            save_device_with_password(config.config_dir, new_device, password)


@click.command(short_help="claim invitation")
@core_config_options
@click.argument("addr", type=BackendInvitationAddr.from_url)
@click.password_option(prompt="Choose a password for the claimed device")
def claim_invitation(config, addr, password, **kwargs):
    """
    Claim a device or user from a invitation
    """
    with cli_exception_handler(config.debug):
        # Disable task monitoring given user prompt will block the coroutine
        trio_run(_claim_invitation, config, addr, password)


async def _list_invitations(config, device):
    async with backend_authenticated_cmds_factory(
        addr=device.organization_addr,
        device_id=device.device_id,
        signing_key=device.signing_key,
        keepalive=config.backend_connection_keepalive,
    ) as cmds:
        rep = await cmds.invite_list()
        if rep["status"] != "ok":
            raise RuntimeError(f"Backend error while listing invitations: {rep}")
        display_statuses = {
            InvitationStatus.READY: click.style("ready", fg="green"),
            InvitationStatus.IDLE: click.style("idle", fg="yellow"),
            InvitationStatus.DELETED: click.style("deleted", fg="red"),
        }
        for invitation in rep["invitations"]:
            display_status = display_statuses[invitation["status"]]
            display_token = invitation["token"].hex
            if invitation["type"] == InvitationType.USER:
                display_type = f"user (email={invitation['claimer_email']})"
            else:  # Device
                display_type = f"device"
            click.echo(f"{display_token}\t{display_status}\t{display_type}")
        if not rep["invitations"]:
            click.echo("No invitations.")


@click.command(short_help="list invitations")
@core_config_and_device_options
def list_invitations(config, device, **kwargs):
    """
    List invitations
    """
    with cli_exception_handler(config.debug):
        trio_run(_list_invitations, config, device)


async def _cancel_invitation(config, device, token):
    async with backend_authenticated_cmds_factory(
        addr=device.organization_addr,
        device_id=device.device_id,
        signing_key=device.signing_key,
        keepalive=config.backend_connection_keepalive,
    ) as cmds:
        rep = await cmds.invite_delete(token=token, reason=InvitationDeletedReason.CANCELLED)
        if rep["status"] != "ok":
            raise RuntimeError(f"Backend error while cancelling invitation: {rep}")
        click.echo("Invitation deleted.")


@click.command(short_help="cancel invitations")
@core_config_and_device_options
@click.argument("token", type=UUID)
def cancel_invitation(config, device, token, **kwargs):
    """
    Cancel invitation
    """
    with cli_exception_handler(config.debug):
        trio_run(_cancel_invitation, config, device, token)
