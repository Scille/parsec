# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import ssl
import trio
import click
from structlog import get_logger
from itertools import count
from collections import defaultdict

from parsec.utils import trio_run
from parsec.cli_utils import cli_exception_handler
from parsec.logging import configure_logging, configure_sentry_logging
from parsec.backend import BackendApp
from parsec.backend.config import (
    BackendConfig,
    MockedBlockStoreConfig,
    PostgreSQLBlockStoreConfig,
    S3BlockStoreConfig,
    SWIFTBlockStoreConfig,
    RAID0BlockStoreConfig,
    RAID1BlockStoreConfig,
    RAID5BlockStoreConfig,
)


logger = get_logger()


def _parse_blockstore_param(value):
    if value.upper() == "MOCKED":
        return MockedBlockStoreConfig()
    elif value.upper() == "POSTGRESQL":
        return PostgreSQLBlockStoreConfig()
    else:
        parts = value.split(":")
        if parts[0].upper() == "S3":
            try:
                endpoint_url, region, bucket, key, secret = parts[1:]
            except ValueError:
                raise click.BadParameter(
                    "Invalid S3 config, must be `s3:<endpoint_url>:<region>:<bucket>:<key>:<secret>`"
                )
            return S3BlockStoreConfig(
                s3_endpoint_url=endpoint_url,
                s3_region=region,
                s3_bucket=bucket,
                s3_key=key,
                s3_secret=secret,
            )

        elif parts[0].upper() == "SWIFT":
            try:
                authurl, tenant, container, user, password = parts[1:]
            except ValueError:
                raise click.BadParameter(
                    "Invalid SWIFT config, must be `swift:<authurl>:<tenant>:<container>:<user>:<password>`"
                )
            return SWIFTBlockStoreConfig(
                swift_authurl=authurl,
                swift_tenant=tenant,
                swift_container=container,
                swift_user=user,
                swift_password=password,
            )
        else:
            raise click.BadParameter(f"Invalid blockstore type `{parts[0]}`")


def _parse_blockstore_params(raw_params):
    raid_configs = defaultdict(list)
    for raw_param in raw_params:
        raw_param_parts = raw_param.split(":", 2)
        if raw_param_parts[0].upper() in ("RAID0", "RAID1", "RAID5") and len(raw_param_parts) == 3:
            raid_mode, raid_node, node_param = raw_param_parts
            try:
                raid_node = int(raid_node)
            except ValueError:
                raise click.BadParameter(f"Invalid node index `{raid_node}` (must be integer)")
        else:
            raid_mode = raid_node = None
            node_param = raw_param
        raid_configs[raid_mode].append((raid_node, node_param))

    if len(raid_configs) != 1:
        config_types = [k if k else v[0][1] for k, v in raid_configs.items()]
        raise click.BadParameter(
            f"Multiple blockstore config with different types: {'/'.join(config_types)}"
        )

    raid_mode, raid_params = list(raid_configs.items())[0]
    if not raid_mode:
        if len(raid_params) == 1:
            return _parse_blockstore_param(raid_params[0][1])
        else:
            raise click.BadParameter("Multiple blockstore configs only available for RAID mode")

    blockstores = []
    for x in count(0):
        if x == len(raid_params):
            break

        x_node_params = [node_param for raid_node, node_param in raid_params if raid_node == x]
        if len(x_node_params) == 0:
            raise click.BadParameter(f"Missing node index `{x}` in RAID config")
        elif len(x_node_params) > 1:
            raise click.BadParameter(f"Multiple configuration for node index `{x}` in RAID config")
        blockstores.append(_parse_blockstore_param(x_node_params[0]))

    if raid_mode.upper() == "RAID0":
        return RAID0BlockStoreConfig(blockstores=blockstores)
    elif raid_mode.upper() == "RAID1":
        return RAID1BlockStoreConfig(blockstores=blockstores)
    elif raid_mode.upper() == "RAID5":
        return RAID5BlockStoreConfig(blockstores=blockstores)
    else:
        raise click.BadParameter(f"Invalid multi blockstore mode `{raid_mode}`")


class DevOption(click.Option):
    def handle_parse_result(self, ctx, opts, args):
        value, args = super().handle_parse_result(ctx, opts, args)
        if value:
            for key, value in (
                ("debug", True),
                ("db", "MOCKED"),
                ("blockstore", ("MOCKED",)),
                ("administration_token", "s3cr3t"),
            ):
                if key not in opts:
                    opts[key] = value

        return value, args


@click.command(short_help="run the server")
@click.option(
    "--host",
    "-H",
    default="127.0.0.1",
    show_default=True,
    envvar="PARSEC_HOST",
    help="Host to listen on",
)
@click.option(
    "--port",
    "-P",
    default=6777,
    type=int,
    show_default=True,
    envvar="PARSEC_PORT",
    help="Port to listen on",
)
@click.option(
    "--db",
    required=True,
    envvar="PARSEC_DB",
    help="Database configuration (mocked in memory, or postgresql uri)",
)
@click.option(
    "--db-drop-deleted-data",
    is_flag=True,
    show_default=True,
    envvar="PARSEC_DB_DROP_DELETED_DATA",
    help="Actually delete data database instead of just marking it has deleted",
)
@click.option(
    "--db-min-connections",
    default=5,
    show_default=True,
    envvar="PARSEC_DB_MIN_CONNECTIONS",
    help="Minimal number of connections to the database if using PostgreSQL",
)
@click.option(
    "--db-max-connections",
    default=7,
    show_default=True,
    envvar="PARSEC_DB_MAX_CONNECTIONS",
    help="Maximum number of connections to the database if using PostgreSQL",
)
@click.option(
    "--blockstore",
    "-b",
    required=True,
    multiple=True,
    callback=lambda ctx, param, value: _parse_blockstore_params(value),
    envvar="PARSEC_BLOCKSTORE",
    help="""Blockstore configuration.
Allowed values:
- `MOCKED`: Mocked in memory
- `POSTGRESQL`: Use the database specified in the `--db` param
- `s3:<endpoint_url>:<region>:<bucket>:<key>:<secret>`: Use S3 storage
- `swift:<authurl>:<tenant>:<container>:<user>:<password>`: Use SWIFT storage
On top of that, multiple blockstore configurations can be provided to form a
RAID0/1/5 cluster. Each configuration must be provided with the form
`<raid_type>:<node>:<config>` with `<raid_type>` RAID0/RAID1/RAID5, <node> a
integer and <config> the MOCKED/POSTGRESQL/S3/SWIFT config.
""",
)
@click.option(
    "--administration-token",
    required=True,
    envvar="PARSEC_ADMINISTRATION_TOKEN",
    help="Secret token to access the administration api",
)
@click.option(
    "--ssl-keyfile",
    type=click.Path(exists=True, dir_okay=False),
    envvar="PARSEC_SSL_KEYFILE",
    help="SSL key file",
)
@click.option(
    "--ssl-certfile",
    type=click.Path(exists=True, dir_okay=False),
    envvar="PARSEC_SSL_CERTFILE",
    help="SSL certificate file",
)
@click.option(
    "--log-level",
    "-l",
    default="WARNING",
    type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR")),
    envvar="PARSEC_LOG_LEVEL",
)
@click.option(
    "--log-format", "-f", type=click.Choice(("CONSOLE", "JSON")), envvar="PARSEC_LOG_FORMAT"
)
@click.option("--log-file", "-o", envvar="PARSEC_LOG_FILE")
@click.option("--log-filter", envvar="PARSEC_LOG_FILTER")
@click.option("--sentry-url", envvar="PARSEC_SENTRY_URL", help="Sentry URL for telemetry report")
@click.option("--debug", is_flag=True, envvar="PARSEC_DEBUG")
@click.option(
    "--dev",
    cls=DevOption,
    is_flag=True,
    is_eager=True,
    help="Equivalent to `--debug --db=MOCKED --blockstore=MOCKED --administration-token=s3cr3t",
)
def run_cmd(
    host,
    port,
    db,
    db_drop_deleted_data,
    db_min_connections,
    db_max_connections,
    blockstore,
    administration_token,
    ssl_keyfile,
    ssl_certfile,
    log_level,
    log_format,
    log_file,
    log_filter,
    sentry_url,
    debug,
    dev,
):
    configure_logging(log_level, log_format, log_file, log_filter)
    if sentry_url:
        configure_sentry_logging(sentry_url)

    with cli_exception_handler(debug):

        config = BackendConfig(
            administration_token=administration_token,
            db_url=db,
            db_drop_deleted_data=db_drop_deleted_data,
            db_min_connections=db_min_connections,
            db_max_connections=db_max_connections,
            blockstore_config=blockstore,
            debug=debug,
        )

        backend = BackendApp(config)

        if ssl_certfile or ssl_keyfile:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            if ssl_certfile:
                ssl_context.load_cert_chain(ssl_certfile, ssl_keyfile)
            else:
                ssl_context.load_default_certs()
        else:
            ssl_context = None

        async def _serve_client(stream):
            if ssl_context:
                stream = trio.SSLStream(stream, ssl_context, server_side=True)

            try:
                await backend.handle_client(stream)

            except Exception as exc:
                # If we are here, something unexpected happened...
                logger.error("Unexpected crash", exc_info=exc)
                await stream.aclose()

        async def _run_backend():
            async with trio.open_nursery() as nursery:
                await backend.init(nursery)

                try:
                    await trio.serve_tcp(_serve_client, port, host=host)

                finally:
                    await backend.teardown()

        print(
            f"Starting Parsec Backend on {host}:{port} (db={config.db_type}, "
            f"blockstore={config.blockstore_config.type})"
        )
        try:
            trio_run(_run_backend, use_asyncio=True)
        except KeyboardInterrupt:
            print("bye ;-)")
