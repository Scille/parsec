# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import click

from parsec.utils import trio_run
from parsec.cli_utils import cli_exception_handler
from parsec.core import logged_core_factory
from parsec.core.logged_core import LoggedCore
from parsec.core.cli.utils import core_config_and_device_options
from parsec.core.types import FsPath


def _parse_destination(core: LoggedCore, destination: str):
    try:
        workspace_name, path = destination.split(":")
    except ValueError:
        workspace_name = destination
        path = None
    if path:
        try:
            path = FsPath(path)
        except ValueError:
            path = FsPath(f"/{path}")

    for workspace in core.user_fs.get_user_manifest().workspaces:
        if workspace.name == workspace_name:
            break
    else:
        raise SystemExit(f"Unknown workspace ({destination})")
    return workspace, path


async def _history(config, device, workspace_and_path, workers):
    async with logged_core_factory(config, device) as core:
        workspace, path = _parse_destination(core, workspace_and_path)
        workspace_fs = core.user_fs.get_workspace(workspace.id)
        version_lister = workspace_fs.get_version_lister()
        versions_list, download_limit_reached = await version_lister.list(
            path, max_manifest_queries=150, **({"workers": workers} if workers is not None else {})
        )
        if not download_limit_reached:
            print("Remaining entries, not showing them.")
        print("Versions:")
        for version in versions_list:
            folder = "\n    is a folder" if version.is_folder else ""
            size = f"\n    {version.size} file" if version.size else ""
            source = f"\n    source: {version.source}" if version.source else ""
            dest = f"\n    destination: {version.destination}" if version.destination else ""
            print(
                f"From {version.early} to {version.late}:\n"
                f"    by {version.creator}, updated at {version.updated}\n"
                f"    entry_id {version.id} at version {version.version}"
                f"    {folder}{size}{source}{dest}"
            )


@click.command(short_help="see history")
@core_config_and_device_options
@click.argument("path")
@click.option("--max-queries", default=400, type=click.INT)
@click.option("--max-workers", default=None, type=click.INT)
def history(config, device, path, max_queries, max_workers, **kwargs):
    """
    Give the history of a path, including workspace, for a given device.
    """
    with cli_exception_handler(config.debug):
        trio_run(_history, config, device, path, max_workers)
