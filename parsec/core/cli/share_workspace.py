# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import click

from parsec.api.protocol import UserID
from parsec.cli_utils import cli_exception_handler
from parsec.core import logged_core_factory
from parsec.core.cli.utils import core_config_and_device_options
from parsec.utils import trio_run


async def _share_workspace(config, device, name, user_id):
    async with logged_core_factory(config, device) as core:
        await core.user_fs.workspace_share(f"/{name}", user_id)


@click.command(short_help="share workspace")
@core_config_and_device_options
@click.argument("workspace_name")
@click.argument("user_id", type=UserID, required=True)
def share_workspace(config, device, workspace_name, user_id, **kwargs):
    """
    Create a new workspace for the given device.
    """
    with cli_exception_handler(config.debug):
        trio_run(_share_workspace, config, device, workspace_name, user_id)
