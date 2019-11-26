#! /usr/bin/env python
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

"""
Create a temporary environment and initialize a test setup for parsec.

Run `tests/scripts/run_test_environment.sh --help` for more information.
"""


import pkg_resources

# Make sure parsec is fully installed (core, backend, dev)
pkg_resources.require("parsec-cloud[all]")

import os
import re
import tempfile
import subprocess

import trio
import click
import psutil

from parsec.utils import trio_run
from parsec.core.types import BackendAddr
from parsec.api.protocol import OrganizationID, DeviceID
from parsec.test_utils import initialize_test_organization

DEFAULT_BACKEND_PORT = 6888
DEFAULT_ADMINISTRATION_TOKEN = "V8VjaXrOz6gUC6ZEHPab0DSsjfq6DmcJ"


# Helpers


async def new_environment(source_file=None):
    export_lines = []
    tempdir = tempfile.mkdtemp()
    if os.name == "nt":
        export = "set"
        env = {"APPDATA": tempdir}
    else:
        export = "export"
        env = {
            "XDG_CACHE_HOME": f"{tempdir}/cache",
            "XDG_DATA_HOME": f"{tempdir}/share",
            "XDG_CONFIG_HOME": f"{tempdir}/config",
        }
    for key, value in env.items():
        await trio.Path(value).mkdir(exist_ok=True)
        os.environ[key] = value
        export_lines.append(f"{export} {key}={value}")

    if source_file is None:
        click.echo(
            """\
[Warning] This script has not been sourced.
Please configure your environment with the following commands:
"""
        )
    else:
        click.echo(
            """\
Your environment will be configured with the following commands:
"""
        )

    for line in export_lines:
        click.echo("   " + line)
    click.echo()

    if source_file is None:
        return

    async with await trio.open_file(source_file, "a") as f:
        for line in export_lines:
            await f.write(line + "\n")


async def configure_mime_types():
    if os.name == "nt":
        return
    XDG_DATA_HOME = os.environ["XDG_DATA_HOME"]
    desktop_file = trio.Path(f"{XDG_DATA_HOME}/applications/parsec.desktop")
    await desktop_file.parent.mkdir(exist_ok=True, parents=True)
    await desktop_file.write_text(
        """\
[Desktop Entry]
Name=Parsec
Exec=parsec core gui %u
Type=Application
Terminal=false
StartupNotify=false
StartupWMClass=Parsec
MimeType=x-scheme-handler/parsec;
"""
    )
    await trio.run_process("update-desktop-database -q".split(), check=False)
    await trio.run_process("xdg-mime default parsec.desktop x-scheme-handler/parsec".split())


async def restart_local_backend(administration_token, backend_port):
    pattern = f"parsec.* backend.* run.* -P {backend_port}"
    command = (
        f"python -Wignore -m parsec.cli backend run -b MOCKED --db MOCKED "
        f"-P {backend_port} --administration-token {administration_token}"
    )

    # Trio does not support subprocess in windows yet

    def _windows_target():
        for proc in psutil.process_iter():
            if "python" in proc.name():
                arguments = " ".join(proc.cmdline())
                if re.search(pattern, arguments):
                    proc.kill()
        backend_process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        for data in backend_process.stdout:
            print(data.decode(), end="")
            break
        backend_process.stdout.close()

    # Windows restart
    if os.name == "nt" or True:
        await trio.to_thread.run_sync(_windows_target)

    # Linux restart
    else:

        await trio.run_process(["pkill", "-f", pattern], check=False)
        backend_process = await trio.open_process(command.split(), stdout=subprocess.PIPE)
        async with backend_process.stdout:
            async for data in backend_process.stdout:
                print(data.decode(), end="")
                break

    # Make sure the backend is actually started
    await trio.sleep(0.2)
    url = f"parsec://localhost:{backend_port}?no_ssl=true"
    return BackendAddr.from_url(url)


@click.command()
@click.option("-B", "--backend-address", type=BackendAddr.from_url)
@click.option("-p", "--backend-port", show_default=True, type=int, default=DEFAULT_BACKEND_PORT)
@click.option("-O", "--organization-id", show_default=True, type=OrganizationID, default="corp")
@click.option("-a", "--alice-device-id", show_default=True, type=DeviceID, default="alice@laptop")
@click.option("-b", "--bob-device-id", show_default=True, type=DeviceID, default="bob@laptop")
@click.option("-o", "--other-device-name", show_default=True, default="pc")
@click.option("-x", "--alice-workspace", show_default=True, default="alice_workspace")
@click.option("-y", "--bob-workspace", show_default=True, default="bob_workspace")
@click.option("-P", "--password", show_default=True, default="test")
@click.option(
    "-T", "--administration-token", show_default=True, default=DEFAULT_ADMINISTRATION_TOKEN
)
@click.option("--force/--no-force", show_default=True, default=False)
@click.option("-e", "--empty", is_flag=True)
@click.option("--source-file", hidden=True)
def main(**kwargs):
    """Create a temporary environment and initialize a test setup for parsec.

    WARNING: it also leaves an in-memory backend running in the background
    on port 6888.

    It is typically a good idea to source this script in order to export the XDG
    variables so the upcoming parsec commands point to the test environment:

        \b
        $ source tests/scripts/run_test_environment.sh

    This scripts create two users, alice and bob who both own two devices,
    laptop and pc. They each have their workspace, respectively
    alice_workspace and bob_workspace, that their sharing with each other.

    The --empty (or -e) argument may be used to bypass the initialization of the
    test environment:

        \b
        $ source tests/scripts/run_test_environment.sh --empty

    This can be used to perform a user or device enrollment on the same machine.
    For instance, consider the following scenario:

        \b
        $ source tests/scripts/run_test_environment.sh
        $ parsec core gui
        # Connect as bob@laptop and register a new device called pc
        # Copy the URL

    Then, in a second terminal:

        \b
        $ source tests/scripts/run_test_environment.sh --empty
        $ xdg-open "<paste the URL here>"  # Or
        $ firefox --no-remote "<paste the URL here>"
        # A second instance of parsec pops-up
        # Enter the token to complete the registration
    """
    trio_run(lambda: amain(**kwargs))


async def amain(
    backend_address,
    backend_port,
    organization_id,
    alice_device_id,
    bob_device_id,
    other_device_name,
    alice_workspace,
    bob_workspace,
    password,
    administration_token,
    force,
    empty,
    source_file,
):
    # Set up the temporary environment
    click.echo()
    await new_environment(source_file)

    # Configure MIME types locally
    await configure_mime_types()

    # Keep the environment empty
    if empty:
        return

    # Start a local backend
    if backend_address is None:
        backend_address = await restart_local_backend(administration_token, backend_port)
        click.echo(
            f"""\
A fresh backend server is now running: {backend_address}
"""
        )
    else:
        click.echo(
            f"""\
Using existing backend: {backend_address}
"""
        )

    # Initialize the test organization
    alice_slugid, other_alice_slugid, bob_slugid = await initialize_test_organization(
        backend_address,
        organization_id,
        alice_device_id,
        bob_device_id,
        other_device_name,
        alice_workspace,
        bob_workspace,
        password,
        administration_token,
        force,
    )

    # Report
    click.echo(
        f"""\
Mount alice and bob drives using:

    $ parsec core run -P {password} -D {alice_slugid}
    $ parsec core run -P {password} -D {other_alice_slugid}
    $ parsec core run -P {password} -D {bob_slugid}
"""
    )


if __name__ == "__main__":
    main()