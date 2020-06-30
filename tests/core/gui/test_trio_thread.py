# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import threading
from unittest.mock import MagicMock

import trio

from parsec.core.gui.trio_thread import (
    JobSchedulerNotAvailable,
    ThreadSafeQtSignal,
    run_trio_thread,
)


def test_on_trio_loop_closed(monkeypatch):
    trio_loop_closed = threading.Event()
    vanilla_trio_run = trio.run

    def patched_trio_run(*args, **kwargs):
        try:
            vanilla_trio_run(*args, **kwargs)
        finally:
            trio_loop_closed.set()

    monkeypatch.setattr("trio.run", patched_trio_run)

    on_success = MagicMock(spec=ThreadSafeQtSignal, args_types=())
    on_error_j1 = MagicMock(spec=ThreadSafeQtSignal, args_types=())
    on_error_j2 = MagicMock(spec=ThreadSafeQtSignal, args_types=())
    with run_trio_thread() as job_scheduler:
        job1 = job_scheduler.submit_job(on_success, on_error_j1, trio.sleep_forever)

        # Stop the trio loop
        job_scheduler.stop()
        trio_loop_closed.wait()

        # Stop is idempotent
        job_scheduler.stop()

        # Cancelling job is still ok
        job1.cancel_and_join()
        on_success.emit.assert_not_called()
        on_error_j1.emit.assert_called_once()
        assert job1.status == "cancelled"
        assert isinstance(job1.exc, trio.Cancelled)

        # New jobs are directly cancelled
        job2 = job_scheduler.submit_job(on_success, on_error_j2, lambda: None)
        on_success.emit.assert_not_called()
        on_error_j2.emit.assert_called_once()
        assert job2.status == "cancelled"
        assert isinstance(job2.exc, JobSchedulerNotAvailable)
