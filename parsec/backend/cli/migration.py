# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import click

from parsec.backend.postgresql import apply_migrations, retrieve_migrations
from parsec.cli_utils import cli_exception_handler, ko, ok, spinner
from parsec.utils import trio_run


def _validate_postgres_db_url(ctx, param, value):
    if not (value.startswith("postgresql://") or value.startswith("postgres://")):
        raise click.BadParameter("Must start with `postgresql://` or `postgres://`")
    return value


@click.command(short_help="Updates database schema")
@click.option(
    "--db",
    required=True,
    callback=_validate_postgres_db_url,
    envvar="PARSEC_DB",
    help="PostgreSQL database url",
)
@click.option("--dry-run", is_flag=True)
@click.option("--debug", is_flag=True, envvar="PARSEC_DEBUG")
def migrate(db, debug, dry_run):
    """
    Updates the database schema
    """
    with cli_exception_handler(debug):
        migrations = retrieve_migrations()

        async def _migrate(db):
            async with spinner("Migrate"):
                result = await apply_migrations(db, migrations, dry_run)

            for migration in result.already_applied:
                click.secho(f"{migration.file_name} (already applied)", fg="white")
            for migration in result.new_apply:
                click.secho(f"{migration.file_name} {ok}", fg="green")
            if result.error:
                migration, msg = result.error
                click.secho(f"{migration.file_name} {ko}: {msg}", fg="red")

        trio_run(_migrate, db, use_asyncio=True)
