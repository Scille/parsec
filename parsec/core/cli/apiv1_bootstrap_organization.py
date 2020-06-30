# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
from pathlib import Path

import click
import pendulum

from parsec.api.data import DeviceCertificateContent, UserCertificateContent, UserProfile
from parsec.api.protocol import DeviceID
from parsec.cli_utils import cli_exception_handler, operation, spinner
from parsec.core.backend_connection import apiv1_backend_anonymous_cmds_factory
from parsec.core.config import get_default_config_dir
from parsec.core.local_device import generate_new_device, save_device_with_password
from parsec.core.types import BackendOrganizationBootstrapAddr
from parsec.crypto import SigningKey
from parsec.logging import configure_logging
from parsec.utils import trio_run


async def _bootstrap_organization(
    debug, device_id, organization_bootstrap_addr, config_dir, force, password
):
    root_signing_key = SigningKey.generate()
    root_verify_key = root_signing_key.verify_key
    organization_addr = organization_bootstrap_addr.generate_organization_addr(root_verify_key)

    device_display = click.style(device_id, fg="yellow")
    device = generate_new_device(device_id, organization_addr, profile=UserProfile.ADMIN)

    with operation(f"Creating locally {device_display}"):
        save_device_with_password(config_dir, device, password, force=force)

    now = pendulum.now()
    user_certificate = UserCertificateContent(
        author=None,
        timestamp=now,
        user_id=device.user_id,
        human_handle=None,
        public_key=device.public_key,
        profile=device.profile,
    ).dump_and_sign(root_signing_key)
    device_certificate = DeviceCertificateContent(
        author=None,
        timestamp=now,
        device_id=device_id,
        device_label=None,
        verify_key=device.verify_key,
    ).dump_and_sign(root_signing_key)

    async with spinner(f"Sending {device_display} to server"):
        async with apiv1_backend_anonymous_cmds_factory(organization_bootstrap_addr) as cmds:
            await cmds.organization_bootstrap(
                organization_id=organization_bootstrap_addr.organization_id,
                bootstrap_token=organization_bootstrap_addr.token,
                root_verify_key=root_verify_key,
                user_certificate=user_certificate,
                device_certificate=device_certificate,
                # Regular certificates compatible with redacted here
                redacted_user_certificate=user_certificate,
                redacted_device_certificate=device_certificate,
            )

    organization_addr_display = click.style(organization_addr.to_url(), fg="yellow")
    click.echo(f"Organization url: {organization_addr_display}")


@click.command(short_help="configure new organization")
@click.argument("device", type=DeviceID, required=True)
@click.option("--addr", "-B", type=BackendOrganizationBootstrapAddr.from_url, required=True)
@click.option("--config-dir", type=click.Path(exists=True, file_okay=False))
@click.option("--force", is_flag=True)
@click.password_option()
def bootstrap_organization(device, addr, config_dir, force, password):
    """
    Configure the organization and register it first user&device.
    """

    config_dir = Path(config_dir) if config_dir else get_default_config_dir(os.environ)
    debug = "DEBUG" in os.environ
    configure_logging(log_level="DEBUG" if debug else "WARNING")

    with cli_exception_handler(debug):
        trio_run(_bootstrap_organization, debug, device, addr, config_dir, force, password)
