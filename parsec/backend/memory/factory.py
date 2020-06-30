# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import math

import trio
from async_generator import asynccontextmanager

from parsec.backend.blockstore import blockstore_factory
from parsec.backend.config import BackendConfig
from parsec.backend.events import EventsComponent
from parsec.backend.memory.block import MemoryBlockComponent
from parsec.backend.memory.invite import MemoryInviteComponent
from parsec.backend.memory.message import MemoryMessageComponent
from parsec.backend.memory.organization import MemoryOrganizationComponent
from parsec.backend.memory.ping import MemoryPingComponent
from parsec.backend.memory.realm import MemoryRealmComponent
from parsec.backend.memory.user import MemoryUserComponent
from parsec.backend.memory.vlob import MemoryVlobComponent
from parsec.event_bus import EventBus


@asynccontextmanager
async def components_factory(config: BackendConfig, event_bus: EventBus):
    (send_events_channel, receive_events_channel) = trio.open_memory_channel(math.inf)

    async def _send_event(event: str, **kwargs):
        await send_events_channel.send((event, kwargs))

    async def _dispatch_event():
        async for event, kwargs in receive_events_channel:
            await trio.sleep(0)
            event_bus.send(event, **kwargs)

    organization = MemoryOrganizationComponent()
    user = MemoryUserComponent(_send_event, event_bus)
    invite = MemoryInviteComponent(_send_event, event_bus)
    message = MemoryMessageComponent(_send_event)
    realm = MemoryRealmComponent(_send_event)
    vlob = MemoryVlobComponent(_send_event)
    ping = MemoryPingComponent(_send_event)
    block = MemoryBlockComponent()
    blockstore = blockstore_factory(config.blockstore_config)
    events = EventsComponent(realm)

    components = {
        "events": events,
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
    for component in (organization, user, invite, message, realm, vlob, ping, block):
        component.register_components(**components)

    async with trio.open_service_nursery() as nursery:
        nursery.start_soon(_dispatch_event)
        try:
            yield components

        finally:
            nursery.cancel_scope.cancel()
