# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import gc
import inspect
import time
import traceback

import structlog
import trio

logger = structlog.get_logger()


def format_stack(coro):
    # Stop when reaching trio modules
    if hasattr(coro, "cr_code"):
        module = inspect.getmodule(coro.cr_code)
        if module and module.__name__.startswith("trio."):
            return

    # Work around https://bugs.python.org/issue32810
    if hasattr(coro, "__class__") and coro.__class__.__name__ in (
        "async_generator_asend",
        "async_generator_athrow",
    ):
        coro, *_ = gc.get_referents(coro)
        if not inspect.isasyncgen(coro):
            return

    # Follow a generator
    if getattr(coro, "ag_frame", None):
        yield from traceback.format_stack(coro.ag_frame)
        yield from format_stack(coro.ag_await)
        return

    # Follow a coroutine
    if getattr(coro, "cr_frame", None):
        yield from traceback.format_stack(coro.cr_frame)
        yield from format_stack(coro.cr_await)
        return

    # Follow a decorated coroutine
    if getattr(coro, "gi_frame", None):
        yield from traceback.format_stack(coro.gi_frame)
        yield from format_stack(coro.gi_yieldfrom)
        return


class TaskMonitoringInstrument(trio.abc.Instrument):
    def __init__(self, cpu_threshold=0.2, io_threshold=0.1, trace_all=False):
        self.start_dict = {}
        self.cpu_threshold = cpu_threshold
        self.io_threshold = io_threshold
        self.trace_all = trace_all

    def get_stacktrace(self, task):
        return "".join(format_stack(task.coro))

    def before_task_step(self, task):
        stack = self.get_stacktrace(task) if self.trace_all else ""
        self.start_dict[task] = (time.monotonic(), time.process_time(), stack)

    def after_task_step(self, task):
        if task not in self.start_dict:
            return

        # Pop the information and compute the deltas
        monotonic_start, process_start, stack_before = self.start_dict.pop(task)
        monotonic_delta = round(time.monotonic() - monotonic_start, 3)
        cpu_delta = round(time.process_time() - process_start, 3)
        io_delta = round(monotonic_delta - cpu_delta, 3)

        # Nothing to report
        if io_delta <= self.io_threshold and cpu_delta <= self.cpu_threshold:
            return

        # Report either a blocking IO or a heavy CPU usage
        status = "Blocking IO" if io_delta > self.io_threshold else "Heavy CPU usage"
        stack_after = self.get_stacktrace(task)
        logger.warning(
            f"{status} detected",
            task_name=task.name,
            total_time=monotonic_delta,
            cpu_time=cpu_delta,
            io_time=io_delta,
            stack_before=stack_before,
            stack_after=stack_after,
        )
