# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Tuple

from pendulum import Pendulum
from pypika import Order, Parameter
from pypika import functions as fn

from parsec.api.protocol import DeviceID, OrganizationID, UserID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.message import BaseMessageComponent
from parsec.backend.postgresql.handler import PGHandler, send_signal
from parsec.backend.postgresql.tables import (
    q_device,
    q_device_internal_id,
    q_organization_internal_id,
    q_user_internal_id,
    t_message,
)
from parsec.backend.postgresql.utils import Query

_q_insert_message = (
    Query.into(t_message)
    .columns("organization", "recipient", "timestamp", "index", "sender", "body")
    .insert(
        q_organization_internal_id(Parameter("$1")),
        q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$2")),
        Parameter("$3"),
        Query.from_(t_message)
        .select(fn.Count("*") + 1)
        .where(
            t_message.recipient
            == q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$2"))
        ),
        q_device_internal_id(organization_id=Parameter("$1"), device_id=Parameter("$4")),
        Parameter("$5"),
    )
    .returning("index")
    .get_sql()
)


_q_get_messages = (
    Query.from_(t_message)
    .select(q_device(_id=t_message.sender).select("device_id"), t_message.timestamp, t_message.body)
    .where(
        t_message.recipient
        == q_user_internal_id(organization_id=Parameter("$1"), user_id=Parameter("$2"))
    )
    .orderby("_id", order=Order.asc)
    .offset(Parameter("$3"))
    .get_sql()
)


async def send_message(conn, organization_id, sender, recipient, timestamp, body):
    index = await conn.fetchval(
        _q_insert_message, organization_id, recipient, timestamp, sender, body
    )

    await send_signal(
        conn,
        BackendEvent.MESSAGE_RECEIVED,
        organization_id=organization_id,
        author=sender,
        recipient=recipient,
        index=index,
    )


class PGMessageComponent(BaseMessageComponent):
    def __init__(self, dbh: PGHandler):
        self.dbh = dbh

    async def send(
        self,
        organization_id: OrganizationID,
        sender: DeviceID,
        recipient: UserID,
        timestamp: Pendulum,
        body: bytes,
    ) -> None:
        async with self.dbh.pool.acquire() as conn, conn.transaction():
            await send_message(conn, organization_id, sender, recipient, timestamp, body)

    async def get(
        self, organization_id: OrganizationID, recipient: UserID, offset: int
    ) -> List[Tuple[DeviceID, Pendulum, bytes]]:
        async with self.dbh.pool.acquire() as conn:
            data = await conn.fetch(_q_get_messages, organization_id, recipient, offset)
        return [(DeviceID(d[0]), d[1], d[2]) for d in data]
