# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio

from parsec.core.backend_connection import BackendNotAvailable
from parsec.core.core_events import CoreEvent
from parsec.core.fs import FSBackendOfflineError


async def freeze_messages_monitor_mockpoint():
    """
    Noop function that could be mocked during tests to be able to freeze the
    monitor coroutine running in background
    """
    pass


async def monitor_messages(user_fs, event_bus, task_status):
    wakeup = trio.Event()

    def _on_message_received(event, index):
        nonlocal wakeup
        wakeup.set()
        # Don't wait for the *actual* awakening to change the status to
        # avoid having a period of time when the awakening is scheduled but
        # not yet notified to task_status
        task_status.awake()

    with event_bus.connect_in_context((CoreEvent.BACKEND_MESSAGE_RECEIVED, _on_message_received)):
        try:
            await user_fs.process_last_messages()
            task_status.started()
            while True:
                task_status.idle()
                await wakeup.wait()
                wakeup = trio.Event()
                await freeze_messages_monitor_mockpoint()
                await user_fs.process_last_messages()

        except FSBackendOfflineError as exc:
            raise BackendNotAvailable from exc
