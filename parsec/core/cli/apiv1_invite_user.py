# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import click

from parsec.api.protocol import UserID
from parsec.cli_utils import cli_exception_handler, spinner
from parsec.core.cli.utils import core_config_and_device_options
from parsec.core.invite_claim import generate_invitation_token, invite_and_create_user
from parsec.core.types import BackendOrganizationClaimUserAddr
from parsec.utils import trio_run


async def _invite_user(config, device, invited_user_id, admin):
    action_addr = BackendOrganizationClaimUserAddr.build(
        organization_addr=device.organization_addr, user_id=invited_user_id
    )
    token = generate_invitation_token()

    action_addr_display = click.style(action_addr.to_url(), fg="yellow")
    token_display = click.style(token, fg="yellow")
    click.echo(f"url: {action_addr_display}")
    click.echo(f"token: {token_display}")

    async with spinner("Waiting for invitation reply"):
        invite_device_id = await invite_and_create_user(
            device=device,
            user_id=invited_user_id,
            token=token,
            is_admin=admin,
            keepalive=config.backend_connection_keepalive,
        )

    display_device = click.style(invite_device_id, fg="yellow")
    click.echo(f"Device {display_device} has been created")


@click.command()
@core_config_and_device_options
@click.option("--admin", is_flag=True)
@click.argument("invited_user_id", type=UserID, required=True)
def invite_user(config, device, admin, invited_user_id, **kwargs):
    with cli_exception_handler(config.debug):
        trio_run(_invite_user, config, device, invited_user_id, admin)
