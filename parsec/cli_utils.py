# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import traceback
from contextlib import contextmanager
from functools import partial

import click
import trio
from async_generator import asynccontextmanager

# Scheme stolen from py-spinners
# MIT License Copyright (c) 2017 Manraj Singh
# (https://github.com/manrajgrover/py-spinners)
SCHEMES = {"dots": {"interval": 80, "frames": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]}}

ok = click.style("✔", fg="green")
ko = click.style("✘", fg="red")


@contextmanager
def operation(txt):

    click.echo(txt, nl=False)
    try:

        yield

    except Exception:
        click.echo(f"\r\033[K{txt} {ko}")
        raise

    else:
        click.echo(f"\r\033[K{txt} {ok}")


@asynccontextmanager
async def spinner(txt, sep=" ", scheme="dots", color="magenta"):
    interval = SCHEMES[scheme]["interval"]
    frames = SCHEMES[scheme]["frames"]
    result = None

    def _render_line(frame):
        # Clear line then re-print it
        click.echo(f"\r\033[K{txt}{sep}{frame}", nl=False)

    async def _update_spinner():
        try:
            i = 1
            while True:
                await trio.sleep(interval / 1000)
                _render_line(click.style(frames[i], fg=color))
                i = (i + 1) % len(frames)
        finally:
            # Last render for result
            _render_line(result)
            click.echo()

    async with trio.open_service_nursery() as nursery:
        _render_line(frames[0])
        nursery.start_soon(_update_spinner)

        try:
            yield

        except Exception:
            result = ko
            raise

        else:
            result = ok

        finally:
            nursery.cancel_scope.cancel()


@contextmanager
def cli_exception_handler(debug):
    try:
        yield debug

    except KeyboardInterrupt:
        click.echo("bye ;-)")
        raise SystemExit(0)

    except Exception as exc:
        click.echo(click.style("Error: ", fg="red") + str(exc))
        if debug:
            raise
        else:
            raise SystemExit(1)


def generate_not_available_cmd(exc, hint=None):
    error_msg = "".join(
        [
            click.style("Not available: ", fg="red"),
            "Importing this module has failed with error:\n\n",
            *traceback.format_exception(exc, exc, exc.__traceback__),
            f"\n\n{hint}\n" if hint else "",
        ]
    )

    @click.command(
        context_settings=dict(ignore_unknown_options=True),
        help=f"Not available{' (' + hint + ')' if hint else ''}",
    )
    @click.argument("args", nargs=-1, type=click.UNPROCESSED)
    def bad_cmd(args):
        raise SystemExit(error_msg)

    return bad_cmd


async def aconfirm(*args, **kwargs):
    return await trio.to_thread.run_sync(partial(click.confirm, *args, **kwargs))


async def aprompt(*args, **kwargs):
    return await trio.to_thread.run_sync(partial(click.prompt, *args, **kwargs))
