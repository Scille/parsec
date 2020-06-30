# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from collections import defaultdict
from contextlib import contextmanager
from enum import Enum
from typing import List

import trio
from structlog import get_logger

logger = get_logger()


class MetaEvent(Enum):
    EVENT_CONNECTED = "event.connected"
    EVENT_DISCONNECTED = "event.disconnected"


class EventWaiter:
    def __init__(self, filter):
        self._filter = filter
        self._event_occured = trio.Event()
        self._event_result = None

    def _cb(self, event: Enum, **kwargs):
        if self._event_occured.is_set():
            return
        if self._filter and not self._filter(event, **kwargs):
            return
        self._event_result = (event, kwargs)
        self._event_occured.set()

    async def wait(self):
        await self._event_occured.wait()
        return self._event_result

    def clear(self):
        self._event_occured = trio.Event()
        self._event_result = None


class EventBus:
    def __init__(self):
        self._event_handlers = defaultdict(list)

    def stats(self):
        return {event: len(cbs) for event, cbs in self._event_handlers.items() if cbs}

    def connection_context(self):
        return EventBusConnectionContext(self)

    def send(self, event: Enum, **kwargs):
        # Do not log meta events (event.connected and event.disconnected)
        if "event_type" not in kwargs:
            logger.debug("Send event", event_type=event, **kwargs)
        for cb in self._event_handlers[event]:
            try:
                cb(event, **kwargs)
            except Exception:
                logger.exception(
                    "Unhandled exception in event bus callback",
                    callback=cb,
                    event_type=event,
                    **kwargs
                )

    @contextmanager
    def waiter_on(self, event: Enum, *, filter=None):
        ew = EventWaiter(filter)
        self.connect(event, ew._cb)
        try:
            yield ew

        finally:
            self.disconnect(event, ew._cb)

    @contextmanager
    def waiter_on_first(self, *events: List[Enum], filter=None):
        ew = EventWaiter(filter)
        for event in events:
            self.connect(event, ew._cb)
        try:
            yield ew

        finally:
            for event in events:
                self.disconnect(event, ew._cb)

    def connect(self, event: Enum, cb):
        self._event_handlers[event].append(cb)
        self.send(MetaEvent.EVENT_CONNECTED, event_type=event)

    @contextmanager
    def connect_in_context(self, *events: List[Enum]):
        for event, cb in events:
            self.connect(event, cb)
        try:
            yield

        finally:
            for event, cb in events:
                self.disconnect(event, cb)

    def disconnect(self, event: Enum, cb):
        self._event_handlers[event].remove(cb)
        self.send(MetaEvent.EVENT_DISCONNECTED, event_type=event)


class EventBusConnectionContext:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.to_disconnect = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.clear()

    def clear(self):
        for event, cb in self.to_disconnect:
            self.event_bus.disconnect(event, cb)
        self.to_disconnect.clear()

    def send(self, event: Enum, **kwargs):
        self.event_bus.send(event, **kwargs)

    def waiter_on(self, event: Enum):
        return self.event_bus.waiter_on(event)

    def waiter_on_first(self, *events: List[Enum]):
        return self.event_bus.waiter_on_first(*events)

    def connect(self, event: Enum, cb):
        self.to_disconnect.append((event, cb))
        self.event_bus.connect(event, cb)

    def connect_in_context(self, *events: List[Enum]):
        return self.event_bus.connect_in_context(*events)

    def disconnect(self, event: Enum, cb):
        self.event_bus.disconnect(event, cb)
        self.to_disconnect.remove((event, cb))
