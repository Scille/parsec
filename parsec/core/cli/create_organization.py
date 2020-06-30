# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os

import click

from parsec.api.protocol import OrganizationID
from parsec.cli_utils import cli_exception_handler, spinner
from parsec.core.backend_connection import apiv1_backend_administration_cmds_factory
from parsec.core.types import BackendAddr, BackendOrganizationBootstrapAddr
from parsec.logging import configure_logging
from parsec.utils import trio_run


async def _create_organization(debug, name, backend_addr, administration_token, expiration_date):
    async with spinner("Creating organization in backend"):
        async with apiv1_backend_administration_cmds_factory(
            backend_addr, administration_token
        ) as cmds:
            rep = await cmds.organization_create(name, expiration_date)
            if rep["status"] != "ok":
                raise RuntimeError(f"Backend refused to create organization: {rep}")
            bootstrap_token = rep["bootstrap_token"]

    organization_addr = BackendOrganizationBootstrapAddr.build(backend_addr, name, bootstrap_token)
    organization_addr_display = click.style(organization_addr.to_url(), fg="yellow")
    click.echo(f"Bootstrap organization url: {organization_addr_display}")


@click.command(short_help="create new organization")
@click.argument("name", required=True, type=OrganizationID)
@click.option("--addr", "-B", required=True, type=BackendAddr.from_url)
@click.option("--administration-token", "-T", required=True)
@click.option("--expiration-date", "-E", default=None, type=click.DateTime())
def create_organization(name, addr, administration_token, expiration_date):
    debug = "DEBUG" in os.environ
    configure_logging(log_level="DEBUG" if debug else "WARNING")
    with cli_exception_handler(debug):
        trio_run(_create_organization, debug, name, addr, administration_token, expiration_date)
