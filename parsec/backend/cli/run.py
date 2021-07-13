# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import ssl
import trio
import click
from structlog import get_logger
from typing import Tuple, Optional
from itertools import count
from collections import defaultdict
import tempfile

from parsec.utils import trio_run
from parsec.cli_utils import cli_exception_handler
from parsec.logging import configure_logging, configure_sentry_logging
from parsec.backend import backend_app_factory
from parsec.backend.config import (
    BackendConfig,
    SmtpEmailConfig,
    MockedEmailConfig,
    MockedBlockStoreConfig,
    PostgreSQLBlockStoreConfig,
    S3BlockStoreConfig,
    SWIFTBlockStoreConfig,
    RAID0BlockStoreConfig,
    RAID1BlockStoreConfig,
    RAID5BlockStoreConfig,
)
from parsec.core.types import BackendAddr


logger = get_logger()

DEFAULT_BACKEND_PORT = 6777
DEFAULT_EMAIL_SENDER = "no-reply@parsec.com"


def _split_with_escaping(txt):
    """
    Simple colon (i.e. `:`) escaping with backslash
    Rules (using slash instead of backslash to avoid weird encoding error):
    `<bs>:` -> `:`
    `<bs><bs>` -> `<bs>`
    `<bs>whatever` -> `<bs>whatever`
    """
    parts = []
    current_part = ""
    escaping = False
    for c in txt:
        if escaping:
            if c not in ("\\", ":"):
                current_part += "\\"
            current_part += c
        elif c == "\\":
            escaping = True
            continue
        elif c == ":":
            parts.append(current_part)
            current_part = ""
        else:
            current_part += c
        escaping = False
    if escaping:
        current_part += "\\"
    parts.append(current_part)
    return parts


def _parse_blockstore_param(value):
    if value.upper() == "MOCKED":
        return MockedBlockStoreConfig()
    elif value.upper() == "POSTGRESQL":
        return PostgreSQLBlockStoreConfig()
    else:
        parts = _split_with_escaping(value)
        if parts[0].upper() == "S3":
            try:
                endpoint_url, region, bucket, key, secret = parts[1:]
            except ValueError:
                raise click.BadParameter(
                    "Invalid S3 config, must be `s3:[<endpoint_url>]:<region>:<bucket>:<key>:<secret>`"
                )
            # Provide https by default to avoid anoying escaping for most cases
            if (
                endpoint_url
                and not endpoint_url.startswith("http://")
                and not endpoint_url.startswith("https://")
            ):
                endpoint_url = f"https://{endpoint_url}"
            return S3BlockStoreConfig(
                s3_endpoint_url=endpoint_url or None,
                s3_region=region,
                s3_bucket=bucket,
                s3_key=key,
                s3_secret=secret,
            )

        elif parts[0].upper() == "SWIFT":
            try:
                auth_url, tenant, container, user, password = parts[1:]
            except ValueError:
                raise click.BadParameter(
                    "Invalid SWIFT config, must be `swift:<auth_url>:<tenant>:<container>:<user>:<password>`"
                )
            # Provide https by default to avoid anoying escaping for most cases
            if (
                auth_url
                and not auth_url.startswith("http://")
                and not auth_url.startswith("https://")
            ):
                auth_url = f"https://{auth_url}"
            return SWIFTBlockStoreConfig(
                swift_authurl=auth_url,
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


def _parse_forward_proto_enforce_https_check_param(
    raw_param: Optional[str]
) -> Optional[Tuple[str, str]]:
    if raw_param is None:
        return None
    try:
        key, value = raw_param.split(":")
    except ValueError:
        raise click.BadParameter(f"Invalid format, should be `<header-name>:<header-value>`")
    # HTTP header key is case-insensitive unlike the header value
    return (key.lower(), value)


class DevOption(click.Option):
    def handle_parse_result(self, ctx, opts, args):
        value, args = super().handle_parse_result(ctx, opts, args)
        if value:

            if "port" in opts:
                backend_addr = "parsec://localhost:" + str(opts["port"])
            else:
                backend_addr = "parsec://localhost:" + str(DEFAULT_BACKEND_PORT)
            for key, value in (
                ("debug", True),
                ("db", "MOCKED"),
                ("backend_addr", backend_addr),
                ("email_sender", "no-reply@parsec.com"),
                ("email_host", "MOCKED"),
                ("blockstore", ("MOCKED",)),
                ("administration_token", "s3cr3t"),
            ):
                if key not in opts:
                    opts[key] = value

        return value, args


@click.command(
    context_settings={"max_content_width": 400},
    short_help="run the server",
    epilog="""Note each parameter has a corresponding environ variable with the `PARSEC_` prefix
(e.g. `--email-port=42` parameter is equivalent to environ variable `PARSEC_EMAIL_PORT=42`).

\b
Parameters can also be specified by using the special environment variable `PARSEC_CMD_ARGS`.
All available command line arguments can be used and environ variables within it will be expanded.
For instance:

    $ DB_URL=postgres:///parsec PARSEC_CMD_ARGS='--db=$DB_URL --host=0.0.0.0' parsec backend run
""",
)
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
    default=DEFAULT_BACKEND_PORT,
    type=int,
    show_default=True,
    envvar="PARSEC_PORT",
    help="Port to listen on",
)
@click.option(
    "--db",
    required=True,
    envvar="PARSEC_DB",
    help="""Database configuration.
Allowed values:
-`MOCKED`: Mocked in memory
-`postgresql://<...>`: Use PostgreSQL database
""",
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
    "--maximum-database-connection-attempts",
    default=10,
    show_default=True,
    envvar="PARSEC_MAXIMUM_DATABASE_CONNECTION_ATTEMPTS",
    help="Maximum number of attempts at connecting to the database (0 means never retry)",
)
@click.option(
    "--pause-before-retry-database-connection",
    default=1.0,
    show_default=True,
    envvar="PARSEC_PAUSE_BEFORE_RETRY_DATABASE_CONNECTION",
    help="Number of seconds before a new attempt at connecting to the database",
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
-`MOCKED`: Mocked in memory
-`POSTGRESQL`: Use the database specified in the `--db` param
-`s3:[<endpoint_url>]:<region>:<bucket>:<key>:<secret>`: Use S3 storage
-`swift:<auth_url>:<tenant>:<container>:<user>:<password>`: Use SWIFT storage

Note endpoint_url/auth_url are considered as https by default (e.g.
`s3:foo.com:[...]` -> https://foo.com).
Escaping must be used to provide a custom scheme (e.g. `s3:http\\://foo.com:[...]`).

On top of that, multiple blockstore configurations can be provided to form a
RAID0/1/5 cluster.

Each configuration must be provided with the form
`<raid_type>:<node>:<config>` with `<raid_type>` RAID0/RAID1/RAID5, `<node>` a
integer and `<config>` the MOCKED/POSTGRESQL/S3/SWIFT config.
""",
)
@click.option(
    "--administration-token",
    required=True,
    envvar="PARSEC_ADMINISTRATION_TOKEN",
    help="Secret token to access the administration api",
)
@click.option(
    "--spontaneous-organization-bootstrap",
    envvar="PARSEC_SPONTANEOUS_ORGANIZATION_BOOTSTRAP",
    is_flag=True,
    help="""Allow organization bootstrap without prior creation.

Without this flag, an organization must be created by administration (see
 `parsec core create_organization` command) before bootstrap can occur.

With this flag, the server allows anybody to bootstrap an organanization
by providing an empty bootstrap token given 1) the organization is not boostrapped yet
and 2) the organization hasn't been created by administration (which would act as a
reservation and change the bootstrap token)
""",
)
@click.option(
    "--organization-bootstrap-webhook",
    envvar="PARSEC_ORGANIZATION_BOOTSTRAP_WEBHOOK",
    help="""URL to notify 3rd party service that a new organization has been bootstrapped.

Each time an organization is bootstrapped, an HTTP POST will be send to the URL
with an `application/json` body with the following fields:
organization_id, device_id, device_label (can be null), human_email (can be null), human_label (can be null)
""",
)
@click.option(
    "--organization-default-users-limit",
    envvar="PARSEC_ORGANIZATION_DEFAULT_USERS_LIMIT",
    help="Default value uses by the system to limit the users by organization",
    type=int,
)
@click.option(
    "--backend-addr",
    envvar="PARSEC_BACKEND_ADDR",
    required=True,
    type=BackendAddr.from_url,
    help="URL to reach this server (typically used in invitation emails)",
)
@click.option(
    "--email-host",
    envvar="PARSEC_EMAIL_HOST",
    required=True,
    help="The host to use for sending email, set to `MOCKED` to output emails to temporary file",
)
@click.option(
    "--email-port",
    envvar="PARSEC_EMAIL_PORT",
    type=int,
    default=25,
    show_default=True,
    help="Port to use for the SMTP server defined in EMAIL_HOST",
)
@click.option(
    "--email-host-user",
    envvar="PARSEC_EMAIL_HOST_USER",
    help="Username to use for the SMTP server defined in EMAIL_HOST",
)
@click.option(
    "--email-host-password",
    envvar="PARSEC_EMAIL_HOST_PASSWORD",
    help=(
        "Password to use for the SMTP server defined in EMAIL_HOST."
        " This setting is used in conjunction with EMAIL_HOST_USER when authenticating to the SMTP server."
    ),
)
@click.option(
    "--email-use-ssl",
    envvar="PARSEC_EMAIL_USE_SSL",
    is_flag=True,
    help=(
        "Whether to use a TLS (secure) connection when talking to the SMTP server."
        " This is used for explicit TLS connections, generally on port 587."
    ),
)
@click.option(
    "--email-use-tls",
    envvar="PARSEC_EMAIL_USE_TLS",
    is_flag=True,
    help=(
        "Whether to use an implicit TLS (secure) connection when talking to the SMTP server."
        " In most email documentation this type of TLS connection is referred to as SSL."
        " It is generally used on port 465."
        " Note that --email-use-tls/--email-use-ssl are mutually exclusive,"
        " so only set one of those settings to True."
    ),
)
@click.option(
    "--email-sender", envvar="PARSEC_EMAIL_SENDER", help="Sender address used in sent emails"
)
@click.option(
    "--forward-proto-enforce-https",
    type=str,
    show_default=True,
    default=None,
    callback=lambda ctx, param, value: _parse_forward_proto_enforce_https_check_param(value),
    envvar="PARSEC_FORWARD_PROTO_ENFORCE_HTTPS",
    help=(
        "Enforce HTTPS by redirecting incoming request that do not comply with the provided header."
        " This is useful when running Parsec behind a forward proxy handing the SSL layer."
        " You should *only* use this setting if you control your proxy or have some other"
        " guarantee that it sets/strips this header appropriately."
        " Typical value for this setting should be `X-Forwarded-Proto:https`."
    ),
)
@click.option(
    "--ssl-keyfile",
    type=click.Path(exists=True, dir_okay=False),
    envvar="PARSEC_SSL_KEYFILE",
    help="SSL key file. This setting enables serving Parsec over SSL.",
)
@click.option(
    "--ssl-certfile",
    type=click.Path(exists=True, dir_okay=False),
    envvar="PARSEC_SSL_CERTFILE",
    help="SSL certificate file. This setting enables serving Parsec over SSL.",
)
@click.option(
    "--log-level",
    "-l",
    default="WARNING",
    show_default=True,
    type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
    envvar="PARSEC_LOG_LEVEL",
)
@click.option(
    "--log-format", "-f", type=click.Choice(("CONSOLE", "JSON")), envvar="PARSEC_LOG_FORMAT"
)
@click.option("--log-file", "-o", envvar="PARSEC_LOG_FILE")
@click.option("--sentry-url", envvar="PARSEC_SENTRY_URL", help="Sentry URL for telemetry report")
@click.option("--debug", is_flag=True, envvar="PARSEC_DEBUG")
@click.option(
    "--dev",
    cls=DevOption,
    is_flag=True,
    is_eager=True,
    help=(
        "Equivalent to `--debug --db=MOCKED"
        " --blockstore=MOCKED --administration-token=s3cr3t"
        " --email-sender=no-reply@parsec.com --email-host=MOCKED"
        " --backend-addr=parsec://localhost:<port>(?no_ssl=False if ssl is not set)`"
    ),
)
def run_cmd(
    host,
    port,
    db,
    db_min_connections,
    db_max_connections,
    maximum_database_connection_attempts,
    pause_before_retry_database_connection,
    blockstore,
    administration_token,
    spontaneous_organization_bootstrap,
    organization_bootstrap_webhook,
    organization_default_users_limit,
    backend_addr,
    email_host,
    email_port,
    email_host_user,
    email_host_password,
    email_use_ssl,
    email_use_tls,
    email_sender,
    forward_proto_enforce_https,
    ssl_keyfile,
    ssl_certfile,
    log_level,
    log_format,
    log_file,
    sentry_url,
    debug,
    dev,
):

    # Start a local backend
    configure_logging(log_level=log_level, log_format=log_format, log_file=log_file)
    if sentry_url:
        configure_sentry_logging(sentry_url)

    with cli_exception_handler(debug):

        if ssl_certfile or ssl_keyfile:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            if ssl_certfile:
                ssl_context.load_cert_chain(ssl_certfile, ssl_keyfile)
            else:
                ssl_context.load_default_certs()
        else:
            ssl_context = None

        if email_host == "MOCKED":
            tmpdir = tempfile.mkdtemp(prefix="tmp-email-folder-")
            if email_sender:
                email_config = MockedEmailConfig(sender=email_sender, tmpdir=tmpdir)
            else:
                email_config = MockedEmailConfig(sender=DEFAULT_EMAIL_SENDER, tmpdir=tmpdir)
        else:
            if not email_sender:
                raise ValueError("--email-sender is required when --email-host is provided")
            email_config = SmtpEmailConfig(
                host=email_host,
                port=email_port,
                host_user=email_host_user,
                host_password=email_host_password,
                use_ssl=email_use_ssl,
                use_tls=email_use_tls,
                sender=email_sender,
            )

        app_config = BackendConfig(
            administration_token=administration_token,
            db_url=db,
            db_min_connections=db_min_connections,
            db_max_connections=db_max_connections,
            blockstore_config=blockstore,
            email_config=email_config,
            ssl_context=True if ssl_context else False,
            forward_proto_enforce_https=forward_proto_enforce_https,
            backend_addr=backend_addr,
            debug=debug,
            bootstrap_webhook_url=organization_bootstrap_webhook,
            spontaneous_bootstrap=spontaneous_organization_bootstrap,
            default_users_limit=organization_default_users_limit,
        )

        click.echo(
            f"Starting Parsec Backend on {host}:{port}"
            f" (db={app_config.db_type}"
            f" blockstore={app_config.blockstore_config.type}"
            f" backend_addr={app_config.backend_addr}"
            f" email_config={str(email_config)})"
        )
        try:
            retry_policy = RetryPolicy(
                maximum_database_connection_attempts, pause_before_retry_database_connection
            )
            trio_run(
                _run_backend, host, port, ssl_context, retry_policy, app_config, use_asyncio=True
            )
        except KeyboardInterrupt:
            click.echo("bye ;-)")


class RetryPolicy:
    def __init__(self, maximum_attempts: int = 10, pause_before_retry: float = 1.0):
        self.maximum_attempts = maximum_attempts
        self.pause_before_retry = pause_before_retry
        self.current_attempt = 0  # No attempt at the moment

    def new_attempt(self):
        self.current_attempt += 1

    def success(self):
        self.current_attempt = 0

    def is_expired(self):
        return self.current_attempt >= self.maximum_attempts

    async def pause(self):
        await trio.sleep(self.pause_before_retry)


async def _run_backend(host, port, ssl_context, retry_policy, app_config):
    # Loop over connection attempts
    while True:
        try:
            # New connection attempt
            retry_policy.new_attempt()

            # Run the backend app (and connect to the database)
            async with backend_app_factory(config=app_config) as backend:

                # Connection is successful, reset the retry policy
                retry_policy.success()

                # Serve backend through TCP
                await _serve_backend(backend, host, port, ssl_context)

        except ConnectionError as exc:
            # The maximum number of attempt is reached
            if retry_policy.is_expired():
                raise
            # Connection with the DB is dead, restart everything
            logger.warning(
                f"Database connection lost ({exc}), retrying in {retry_policy.pause_before_retry} seconds"
            )
            await retry_policy.pause()


async def _serve_backend(backend, host, port, ssl_context):
    # Client handler
    async def _serve_client(stream):
        if ssl_context:
            stream = trio.SSLStream(stream, ssl_context, server_side=True)

        try:
            await backend.handle_client(stream)

        except ConnectionError:
            # Should be handled by the reconnection logic (see `_run_and_retry_back`)
            raise

        except Exception:
            # If we are here, something unexpected happened...
            logger.exception("Unexpected crash")
            await stream.aclose()

    # Provide a service nursery so multi-errors errors are handled
    async with trio.open_service_nursery() as nursery:
        await trio.serve_tcp(_serve_client, port, handler_nursery=nursery, host=host)
