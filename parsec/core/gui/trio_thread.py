# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import threading
from contextlib import asynccontextmanager
from inspect import iscoroutinefunction, signature

import trio
from structlog import get_logger
from parsec.core.fs import FSError
from parsec.core.mountpoint import MountpointError
from parsec.utils import split_multi_error


logger = get_logger()


class JobResultError(Exception):
    def __init__(self, status, **kwargs):
        self.status = status
        self.params = kwargs
        super().__init__(status, kwargs)


class JobSchedulerNotAvailable(Exception):
    pass


class QtToTrioJob:
    def __init__(self, fn, args, kwargs, qt_on_success, qt_on_error):
        self._qt_on_success = qt_on_success
        self._qt_on_error = qt_on_error
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.cancel_scope = None
        self._started = trio.Event()
        self._done = threading.Event()
        self.status = None
        self.ret = None
        self.exc = None

    def __str__(self):
        return f"{self._fn.__name__}"

    @property
    def arguments(self):
        bound_arguments = signature(self._fn).bind(*self._args, **self._kwargs)
        bound_arguments.apply_defaults()
        return bound_arguments.arguments

    def is_finished(self):
        return self._done.is_set()

    def is_cancelled(self):
        return self.status == "cancelled"

    def is_ok(self):
        return self.status == "ok"

    def is_errored(self):
        return self.status == "ko"

    def is_crashed(self):
        return self.status == "crashed"

    async def __call__(self, *, task_status=trio.TASK_STATUS_IGNORED):
        assert self.status is None
        with trio.CancelScope() as self.cancel_scope:
            task_status.started()
            self._started.set()

            try:
                if not iscoroutinefunction(self._fn):
                    result = self._fn(*self._args, **self._kwargs)
                else:
                    result = await self._fn(*self._args, **self._kwargs)
                self.set_result(result)

            except Exception as exc:
                self.set_exception(exc)

            except trio.Cancelled as exc:
                self.set_cancelled(exc)
                raise

            except trio.MultiError as exc:
                cancelled_errors, other_exceptions = split_multi_error(exc)
                if other_exceptions:
                    self.set_exception(other_exceptions)
                else:
                    self.set_cancelled(cancelled_errors)
                if cancelled_errors:
                    raise cancelled_errors

    def set_result(self, result):
        self.status = "ok"
        self.ret = result
        self._set_done()

    def set_cancelled(self, exc):
        self.status = "cancelled"
        self.exc = exc
        self._set_done()

    def set_exception(self, exc):
        if isinstance(exc, JobResultError):
            self.status = exc.status
            self.exc = exc
        elif isinstance(exc, (FSError, MountpointError)):
            self.status = "ko"
            self.exc = exc
        else:
            logger.exception("Uncatched error", exc_info=exc)
            wrapped = JobResultError("crashed", exc=exc, info=f"Unexpected error: {repr(exc)}")
            wrapped.__traceback__ = exc.__traceback__
            self.status = wrapped.status
            self.exc = wrapped
        self._set_done()

    def _set_done(self):
        self._done.set()
        signal = self._qt_on_success if self.is_ok() else self._qt_on_error
        if signal.signal.endswith("(PyQt_PyObject)"):
            signal.emit(self)
        else:
            signal.emit()

    def cancel(self):
        self.cancel_scope.cancel()

    def cancel_and_join(self):
        # XXX: Quick fix, joining cannot be done in a callback
        self.cancel_scope.cancel()


class QtToTrioJobScheduler:
    def __init__(self, nursery):
        self.nursery = nursery
        self._throttling_scheduled_jobs = {}
        self._throttling_last_executed = {}

    def submit_throttled_job(
        self, throttling_id: str, delay: float, qt_on_success, qt_on_error, fn, *args, **kwargs
    ):
        """
        Throttle execution: immediatly execute `fn` unless a job with a similar
        `throttling_id` it has been already executed in the last `delay` seconds,
        in which case we schedule the execution to occur when the delay is elapsed.
        Submitting a job with an already sheduled `throttling_id` will lead to
        a single execution of the last provided job parameters at the soonest delay.
        """

        async def _throttled_execute(job, task_status=trio.TASK_STATUS_IGNORED):
            # Only modify `_throttling_scheduled_jobs` from the trio
            # thread to avoid concurrent acces with the Qt thread
            # Note we might be overwritting another job here, it is fine given
            # we only want to execute the last scheduled one
            self._throttling_scheduled_jobs[throttling_id] = job
            task_status.started()

            # Sleep the throttle delay, if last execution occurred long enough
            # this will equivalent of doing `trio.sleep(0)`
            last_executed = self._throttling_last_executed.get(throttling_id, 0)
            await trio.sleep_until(last_executed + delay)

            # It is possible our job has been executed by another `_throttled_execute`
            # that had a shorter delay. In such case we have nothing more to do.
            last_executed = self._throttling_last_executed.get(throttling_id, 0)
            if last_executed + delay > trio.current_time():
                return
            job = self._throttling_scheduled_jobs.pop(throttling_id, None)
            if not job:
                return

            # Actually start the job execution
            self._throttling_last_executed[throttling_id] = trio.current_time()
            try:
                await self.nursery.start(job)
            except trio.BrokenResourceError:
                # Job scheduler has been closed, nothing more can be done
                pass

        # Create the job but don't execute it: we have to handle throttle first !
        job = QtToTrioJob(fn, args, kwargs, qt_on_success, qt_on_error)
        if self.nursery._closed:
            job.set_cancelled(JobSchedulerNotAvailable())
        else:
            self.nursery.start_soon(_throttled_execute, job)
        return job

    def submit_job(self, qt_on_success, qt_on_error, fn, *args, **kwargs):
        job = QtToTrioJob(fn, args, kwargs, qt_on_success, qt_on_error)
        if self.nursery._closed:
            job.set_cancelled(JobSchedulerNotAvailable())
        else:
            self.nursery.start_soon(job)
        return job

    def run_sync(self, fn, *args):
        if self.nursery._closed:
            raise JobSchedulerNotAvailable
        return fn(*args)


@asynccontextmanager
async def run_trio_job_scheduler():
    async with trio.open_service_nursery() as nursery:
        yield QtToTrioJobScheduler(nursery)
