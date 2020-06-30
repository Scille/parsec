# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import sys

import click

from parsec._version import __version__
from parsec.cli_utils import generate_not_available_cmd

try:
    from parsec.core.cli import core_cmd
except ImportError as exc:
    core_cmd = generate_not_available_cmd(exc)


try:
    from parsec.backend.cli import backend_cmd
except ImportError as exc:
    backend_cmd = generate_not_available_cmd(exc)


@click.group()
@click.version_option(version=__version__, prog_name="parsec")
def cli():
    pass


cli.add_command(core_cmd, "core")
cli.add_command(backend_cmd, "backend")

# Add support for PARSEC_CMD_ARGS env var

vanilla_cli_main = cli.main


def patched_cli_main(args=None, **kwargs):
    if args is None:
        args = sys.argv[1:]

    raw_extra_args = os.environ.get("PARSEC_CMD_ARGS", "")
    args += [os.path.expandvars(x) for x in raw_extra_args.split()]

    return vanilla_cli_main(args=args, **kwargs)


cli.main = patched_cli_main


if __name__ == "__main__":
    cli()
