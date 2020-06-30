# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


import attr
import trio
from async_generator import asynccontextmanager
from pendulum import Pendulum
from structlog import get_logger

from parsec import service_nursery
from parsec.monitoring import TaskMonitoringInstrument

__all__ = [
    "timestamps_in_the_ballpark",
    "start_task",
    "trio_run",
    "open_service_nursery",
    "split_multi_error",
]

logger = get_logger()
TIMESTAMP_MAX_DT = 30 * 60


def timestamps_in_the_ballpark(ts1: Pendulum, ts2: Pendulum, max_dt=TIMESTAMP_MAX_DT) -> bool:
    """
    Useful to compare signed message timestamp with the one stored by the
    backend.
    """
    return abs((ts1 - ts2).total_seconds()) < max_dt


# Task status


@attr.s
class TaskStatus:

    # Internal state

    _cancel_scope = attr.ib(default=None)
    _started_value = attr.ib(default=None)
    _finished_event = attr.ib(factory=trio.Event)

    def _set_cancel_scope(self, scope):
        self._cancel_scope = scope

    def _set_started_value(self, value):
        self._started_value = value

    def _set_finished(self):
        self._finished_event.set()

    # Properties

    @property
    def cancel_called(self):
        return self._cancel_scope.cancel_called

    @property
    def finished(self):
        return self._finished_event.is_set()

    @property
    def value(self):
        return self._started_value

    # Methods

    def cancel(self):
        self._cancel_scope.cancel()

    async def join(self):
        await self._finished_event.wait()
        await trio.sleep(0)  # Checkpoint

    async def cancel_and_join(self):
        self.cancel()
        await self.join()

    # Class methods

    @classmethod
    async def wrap_task(cls, corofn, *args, task_status=trio.TASK_STATUS_IGNORED):
        status = cls()
        try:
            async with trio.open_service_nursery() as nursery:
                status._set_cancel_scope(nursery.cancel_scope)
                value = await nursery.start(corofn, *args)
                status._set_started_value(value)
                task_status.started(status)
        finally:
            status._set_finished()


async def start_task(nursery, corofn, *args, name=None) -> TaskStatus:
    """Equivalent to nursery.start but return a TaskStatus object.

    This object can be used to cancel and/or join the task.
    It also contains the started value set by `task_status.started()`.
    """
    return await nursery.start(TaskStatus.wrap_task, corofn, *args, name=name)


def trio_run(async_fn, *args, use_asyncio=False, monitor_tasks=True):
    if use_asyncio:
        # trio_asyncio is an optional dependency
        import trio_asyncio

        return trio_asyncio.run(async_fn, *args)
    instruments = (TaskMonitoringInstrument(),) if monitor_tasks else ()
    return trio.run(async_fn, *args, instruments=instruments)


# MultiError handling


def split_multi_error(exc):
    def is_cancelled(exc):
        return exc if isinstance(exc, trio.Cancelled) else None

    def not_cancelled(exc):
        return None if isinstance(exc, trio.Cancelled) else exc

    cancelled_errors = trio.MultiError.filter(is_cancelled, exc)
    other_exceptions = trio.MultiError.filter(not_cancelled, exc)
    return cancelled_errors, other_exceptions


async def check_cancellation(exc):
    # If the multierror contains a Cancelled(), that means a cancellation
    # has leaked outside of the nursery. This indicates the current scope
    # has likely been cancelled. In this case, there is no point in handling
    # both the cancelled and regular exceptions: simply check for cancellation
    # and let a new Cancelled() raise if necessary, discarding the original
    # exception.
    cancelled_errors, _ = split_multi_error(exc)
    if cancelled_errors:
        await trio.hazmat.checkpoint_if_cancelled()


def collapse_multi_error(multierror):
    # Pick the first exception as the reference exception
    pick = multierror.exceptions[0]
    try:
        # Craft a new a name to indicate the exception is collapsed
        name = f"{type(pick).__name__}AndFriends"
        # Init method should be ignored as it might get called by `MultiError([result])`
        attrs = {"__init__": lambda *args, **kwargs: None}
        # Craft the specific class, inheriting from MultiError
        cls = type(name, (type(pick), trio.MultiError), attrs)
        # Instantiate the instance
        result = cls()
        # Replicate the picked exception inner state
        result.__dict__.update(pick.__dict__)
        result.args = pick.args
        # Replicate the multierror inner state
        result.exceptions = multierror.exceptions
        result.__cause__ = multierror.__cause__
        result.__context__ = multierror.__context__
        result.__traceback__ = multierror.__traceback__
        # Supress context, we do not want the collapsing to appear in the stacktrace
        result.__suppress_context__ = True
        return result
    except Exception:
        # Something went wrong while collapsing
        logger.exception("Cound not create a collapsed exception")
        return pick


@asynccontextmanager
async def open_service_nursery():
    """Open a service nursery.

    This nursery does not raise MultiError exceptions.
    Instead, it collapses the MultiError into a single exception.
    More precisely, the first exception of the MultiError is raised,
    patched with extra MultiError capabilities.
    """
    try:
        async with service_nursery.open_service_nursery() as nursery:
            yield nursery
    except trio.MultiError as exc:
        # Re-raise a Cancelled() if the exception contains a Cancelled()
        await check_cancellation(exc)
        # Collapse the MultiError into a single exception
        logger.exception("A MultiError has been detected")
        raise collapse_multi_error(exc)


async def cancel_and_checkpoint(scope):
    scope.cancel()
    await trio.hazmat.checkpoint_if_cancelled()


# Add it to trio
trio.open_service_nursery = open_service_nursery
trio.CancelScope.cancel_and_checkpoint = cancel_and_checkpoint
