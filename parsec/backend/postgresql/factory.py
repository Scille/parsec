# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio
import triopg
from async_generator import asynccontextmanager
from typing import Optional

from parsec.event_bus import EventBus
from parsec.backend.config import BackendConfig
from parsec.backend.events import EventsComponent
from parsec.backend.blockstore import blockstore_factory
from parsec.backend.webhooks import WebhooksComponent
from parsec.backend.http import HTTPComponent
from parsec.backend.postgresql.handler import PGHandler, send_signal
from parsec.backend.postgresql.stats import PGStatsComponent
from parsec.backend.postgresql.organization import PGOrganizationComponent
from parsec.backend.postgresql.ping import PGPingComponent
from parsec.backend.postgresql.user import PGUserComponent
from parsec.backend.postgresql.invite import PGInviteComponent
from parsec.backend.postgresql.message import PGMessageComponent
from parsec.backend.postgresql.realm import PGRealmComponent
from parsec.backend.postgresql.vlob import PGVlobComponent
from parsec.backend.postgresql.block import PGBlockComponent
from parsec.backend.backend_events import BackendEvent


@asynccontextmanager
async def components_factory(config: BackendConfig, event_bus: EventBus):
    dbh = PGHandler(
        config.db_url,
        config.db_min_connections,
        config.db_max_connections,
        config.db_first_tries_number,
        config.db_first_tries_sleep,
        event_bus,
    )

    async def _send_event(
        event: BackendEvent, conn: Optional[triopg._triopg.TrioConnectionProxy] = None, **kwargs
    ):
        if conn is None:
            async with dbh.pool.acquire() as conn:
                await send_signal(conn, event, **kwargs)
        else:
            await send_signal(conn, event, **kwargs)

    webhooks = WebhooksComponent(config)
    http = HTTPComponent(config)
    stats = PGStatsComponent(dbh)
    organization = PGOrganizationComponent(dbh, webhooks, stats)
    user = PGUserComponent(dbh, event_bus)
    invite = PGInviteComponent(dbh, event_bus, config)
    message = PGMessageComponent(dbh)
    realm = PGRealmComponent(dbh, stats)
    vlob = PGVlobComponent(dbh)
    ping = PGPingComponent(dbh)
    blockstore = blockstore_factory(config.blockstore_config, postgresql_dbh=dbh)
    block = PGBlockComponent(dbh, blockstore)
    events = EventsComponent(realm, send_event=_send_event)

    async with trio.open_service_nursery() as nursery:
        await dbh.init(nursery)
        try:
            yield {
                "events": events,
                "webhooks": webhooks,
                "http": http,
                "stats": stats,
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

        finally:
            await dbh.teardown()
