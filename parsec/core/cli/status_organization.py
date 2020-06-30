# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os

import click

from parsec.api.protocol import OrganizationID
from parsec.cli_utils import cli_exception_handler
from parsec.core.backend_connection import apiv1_backend_administration_cmds_factory
from parsec.core.types import BackendAddr
from parsec.logging import configure_logging
from parsec.utils import trio_run


async def _status_organization(name, backend_addr, administration_token):
    async with apiv1_backend_administration_cmds_factory(
        backend_addr, administration_token
    ) as cmds:
        stats = await cmds.organization_status(name)
    for key, value in stats.items():
        click.echo(f"{key}: {value}")


@click.command(short_help="status new organization")
@click.argument("name", required=True, type=OrganizationID)
@click.option("--addr", "-B", required=True, type=BackendAddr.from_url)
@click.option("--administration-token", "-T", required=True)
def status_organization(name, addr, administration_token):
    debug = "DEBUG" in os.environ
    configure_logging(log_level="DEBUG" if debug else "WARNING")

    with cli_exception_handler(debug):
        trio_run(_status_organization, name, addr, administration_token)
