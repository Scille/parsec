# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import platform

import click

from parsec.cli_utils import cli_exception_handler, operation, spinner
from parsec.core.cli.utils import core_config_options
from parsec.core.invite_claim import claim_user as actual_claim_user
from parsec.core.local_device import save_device_with_password
from parsec.core.types import BackendOrganizationClaimUserAddr
from parsec.utils import trio_run


async def _claim_user(config, organization_addr, new_device_id, token, password):

    async with spinner("Waiting for referee to reply"):
        device = await actual_claim_user(
            organization_addr=organization_addr,
            new_device_id=new_device_id,
            token=token,
            keepalive=config.backend_connection_keepalive,
        )

    device_display = click.style(new_device_id, fg="yellow")
    with operation(f"Saving locally {device_display}"):
        save_device_with_password(config.config_dir, device, password)


@click.command()
@click.argument("device-name", default=platform.node())
@core_config_options
@click.option("--addr", required=True, type=BackendOrganizationClaimUserAddr.from_url)
@click.option("--token")
@click.password_option()
def claim_user(config, addr, device_name, token, password, **kwargs):
    try:
        new_device_id = addr.user_id.to_device_id(device_name)
    except ValueError:
        raise SystemExit(f"Invalid device name value `{device_name}`")

    if token and addr.token:
        raise SystemExit("token already specified in the address")
    token = token or addr.token
    if not token:
        raise SystemExit("Missing token value")

    debug = "DEBUG" in os.environ
    with cli_exception_handler(debug):
        trio_run(_claim_user, config, addr.to_organization_addr(), new_device_id, token, password)
