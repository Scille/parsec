# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import math
from enum import Enum
from typing import Tuple, Dict

import trio
from async_generator import asynccontextmanager

from parsec.event_bus import EventBus
from parsec.utils import open_service_nursery
from parsec.backend.config import BackendConfig
from parsec.backend.blockstore import blockstore_factory
from parsec.backend.events import EventsComponent
from parsec.backend.memory.organization import MemoryOrganizationComponent
from parsec.backend.memory.ping import MemoryPingComponent
from parsec.backend.memory.user import MemoryUserComponent
from parsec.backend.memory.invite import MemoryInviteComponent
from parsec.backend.memory.message import MemoryMessageComponent
from parsec.backend.memory.realm import MemoryRealmComponent
from parsec.backend.memory.vlob import MemoryVlobComponent
from parsec.backend.memory.block import MemoryBlockComponent
from parsec.backend.webhooks import WebhooksComponent
from parsec.backend.http import HTTPComponent


@asynccontextmanager
async def components_factory(config: BackendConfig, event_bus: EventBus):
    send_events_channel, receive_events_channel = trio.open_memory_channel[
        Tuple[Enum, Dict[str, object]]
    ](math.inf)

    async def _send_event(event: Enum, **kwargs):
        await send_events_channel.send((event, kwargs))

    async def _dispatch_event():
        async for event, kwargs in receive_events_channel:
            await trio.sleep(0)
            event_bus.send(event, **kwargs)

    webhooks = WebhooksComponent(config)
    http = HTTPComponent(config)
    organization = MemoryOrganizationComponent(_send_event, webhooks)
    user = MemoryUserComponent(_send_event, event_bus)
    invite = MemoryInviteComponent(_send_event, event_bus, config)
    message = MemoryMessageComponent(_send_event)
    realm = MemoryRealmComponent(_send_event)
    vlob = MemoryVlobComponent(_send_event)
    ping = MemoryPingComponent(_send_event)
    block = MemoryBlockComponent()
    blockstore = blockstore_factory(config.blockstore_config)
    events = EventsComponent(realm, send_event=_send_event)

    components = {
        "events": events,
        "webhooks": webhooks,
        "http": http,
        "organization": organization,
        "user": user,
        "invite": invite,
        "message": message,
        "realm": realm,
        "vlob": vlob,
        "ping": ping,
        "block": block,
        "blockstore": blockstore,
    }
    for component in components.values():
        method = getattr(component, "register_components", None)
        if method is not None:
            method(**components)

    async with open_service_nursery() as nursery:
        nursery.start_soon(_dispatch_event)
        try:
            yield components

        finally:
            nursery.cancel_scope.cancel()
