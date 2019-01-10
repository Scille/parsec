import os
import trio
import trio_asyncio
import click
from structlog import get_logger

from parsec.cli_utils import spinner, cli_exception_handler
from parsec.logging import configure_logging, configure_sentry_logging
from parsec.backend import BackendApp, config_factory
from parsec.backend.drivers.postgresql import init_db


__all__ = ("backend_cmd", "init_cmd", "run_cmd")


logger = get_logger()


@click.command(short_help="init the database")
@click.option("--db", required=True, help="PostgreSQL database url")
@click.option("--force", "-f", is_flag=True)
def init_cmd(db, force):
    """
    Initialize a new backend's PostgreSQL database.
    """
    if not db.startswith("postgresql://") or db.startswith("postgres://"):
        raise SystemExit("Can only initialize a PostgreSQL database.")

    debug = "DEBUG" in os.environ
    with cli_exception_handler(debug):

        async def _init_db(db, force):
            async with spinner("Initializing database"):
                already_initialized = await init_db(db, force)
            if already_initialized:
                click.echo("Database already initialized, nothing to do.")

        trio_asyncio.run(_init_db, db, force)


@click.command(short_help="run the server")
@click.option("--host", "-H", default="127.0.0.1", help="Host to listen on (default: 127.0.0.1)")
@click.option("--port", "-P", default=6777, type=int, help=("Port to listen on (default: 6777)"))
@click.option("--db", default="MOCKED", help="Database configuration (default: mocked in memory)")
@click.option(
    "--blockstore",
    "-b",
    default="MOCKED",
    type=click.Choice(("MOCKED", "POSTGRESQL", "S3", "SWIFT", "RAID1")),
    help="Block store the clients should write into (default: mocked in memory). Set environment variables accordingly.",
)
@click.option(
    "--ssl-keyfile", default=None, type=click.Path(exists=True, dir_okay=False), help="SSL key file"
)
@click.option(
    "--ssl-certfile",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="SSL certificate file",
)
@click.option(
    "--log-level", "-l", default="WARNING", type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR"))
)
@click.option("--log-format", "-f", default="CONSOLE", type=click.Choice(("CONSOLE", "JSON")))
@click.option("--log-file", "-o")
@click.option("--log-filter", default=None)
def run_cmd(
    host,
    port,
    db,
    blockstore,
    ssl_keyfile,
    ssl_certfile,
    log_level,
    log_format,
    log_file,
    log_filter,
):
    configure_logging(log_level, log_format, log_file, log_filter)

    debug = "DEBUG" in os.environ
    with cli_exception_handler(debug):

        try:
            config = config_factory(
                blockstore_type=blockstore, db_url=db, debug=debug, environ=os.environ
            )

        except ValueError as exc:
            raise ValueError(f"Invalid configuration: {exc}")

        if config.sentry_url:
            configure_sentry_logging(config.sentry_url)

        backend = BackendApp(config)

        if ssl_certfile or ssl_keyfile:
            ssl_context = trio.ssl.create_default_context(trio.ssl.Purpose.SERVER_AUTH)
            if ssl_certfile:
                ssl_context.load_cert_chain(ssl_certfile, ssl_keyfile)
            else:
                ssl_context.load_default_certs()
        else:
            ssl_context = None

        async def _serve_client(stream):
            if ssl_context:
                stream = trio.ssl.SSLStream(stream, ssl_context, server_side=True)

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
            f"Starting Parsec Backend on {host}:{port} (db={config.db_type}, blockstore={config.blockstore_config.type})"
        )
        try:
            trio_asyncio.run(_run_backend)
        except KeyboardInterrupt:
            print("bye ;-)")


@click.group()
def backend_cmd():
    pass


backend_cmd.add_command(run_cmd, "run")
backend_cmd.add_command(init_cmd, "init")


if __name__ == "__main__":
    backend_cmd()
