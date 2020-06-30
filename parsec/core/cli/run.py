# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pathlib import Path

import click
import trio
from pendulum import Pendulum
from pendulum import parse as pendulum_parse

from parsec.cli_utils import cli_exception_handler, generate_not_available_cmd
from parsec.core import logged_core_factory
from parsec.core.cli.utils import core_config_and_device_options, core_config_options
from parsec.utils import trio_run

try:
    from parsec.core.gui import run_gui as _run_gui

except ImportError as exc:
    run_gui = generate_not_available_cmd(exc)

else:

    @click.command(short_help="run parsec GUI")
    # Let the GUI handle the parsing of the url to display dialog on error
    @click.argument("url", required=False)
    @click.option("--diagnose", "-d", is_flag=True)
    @core_config_options
    def run_gui(config, url, diagnose, **kwargs):
        """
        Run parsec GUI
        """
        config = config.evolve(mountpoint_enabled=True)
        _run_gui(config, start_arg=url, diagnose=diagnose)


async def _run_mountpoint(config, device, timestamp: Pendulum = None):
    config = config.evolve(mountpoint_enabled=True)
    async with logged_core_factory(config, device):
        display_device = click.style(device.device_id, fg="yellow")
        mountpoint_display = click.style(str(config.mountpoint_base_dir.absolute()), fg="yellow")
        click.echo(f"{display_device}'s drive mounted at {mountpoint_display}")

        await trio.sleep_forever()


@click.command(short_help="run parsec mountpoint")
@core_config_and_device_options
@click.option("--mountpoint", "-m", type=click.Path(exists=False))
@click.option("--timestamp", "-t", type=lambda t: pendulum_parse(t, tz="local"))
def run_mountpoint(config, device, mountpoint, timestamp, **kwargs):
    """
    Expose device's parsec drive on the given mountpoint.
    """
    config = config.evolve(mountpoint_enabled=True)
    if mountpoint:
        config = config.evolve(mountpoint_base_dir=Path(mountpoint))
    with cli_exception_handler(config.debug):
        trio_run(_run_mountpoint, config, device, timestamp)
