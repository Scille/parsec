# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import platform

import click

from parsec.api.protocol import HumanHandle
from parsec.cli_utils import aprompt, cli_exception_handler, operation, spinner
from parsec.core.backend_connection import apiv1_backend_anonymous_cmds_factory
from parsec.core.cli.utils import core_config_options
from parsec.core.invite import bootstrap_organization as do_bootstrap_organization
from parsec.core.local_device import save_device_with_password
from parsec.core.types import BackendOrganizationBootstrapAddr
from parsec.utils import trio_run


async def _bootstrap_organization(config, addr, password, force):
    label = await aprompt("User fullname")
    email = await aprompt("User email")
    human_handle = HumanHandle(email=email, label=label)
    device_label = await aprompt("Device label", default=platform.node())

    async with apiv1_backend_anonymous_cmds_factory(addr=addr) as cmds:
        async with spinner("Bootstrapping organization in the backend"):
            new_device = await do_bootstrap_organization(
                cmds=cmds, human_handle=human_handle, device_label=device_label
            )

        device_display = click.style(new_device.slughash, fg="yellow")
        with operation(f"Saving device {device_display}"):
            save_device_with_password(
                config_dir=config.config_dir, device=new_device, password=password, force=force
            )


@click.command(short_help="configure new organization")
@core_config_options
@click.argument("addr", type=BackendOrganizationBootstrapAddr.from_url)
@click.password_option(prompt="Choose a password for the device")
@click.option("--force", is_flag=True)
def bootstrap_organization(config, addr, password, force, **kwargs):
    """
    Configure the organization and register it first user&device.
    """
    with cli_exception_handler(config.debug):
        # Disable task monitoring given user prompt will block the coroutine
        trio_run(_bootstrap_organization, config, addr, password, force)
