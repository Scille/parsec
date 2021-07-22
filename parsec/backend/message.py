# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from typing import List, Tuple
from pendulum import DateTime

from parsec.api.protocol import DeviceID, UserID, OrganizationID
from parsec.api.protocol import message_get_serializer
from parsec.backend.utils import catch_protocol_errors, api


class MessageError(Exception):
    pass


class BaseMessageComponent:
    @api("message_get")
    @catch_protocol_errors
    async def api_message_get(self, client_ctx, msg):
        msg = message_get_serializer.req_load(msg)

        offset = msg["offset"]
        messages = await self.get(client_ctx.organization_id, client_ctx.user_id, offset)

        return message_get_serializer.rep_dump(
            {
                "status": "ok",
                "messages": [
                    {"count": i, "body": body, "timestamp": timestamp, "sender": sender}
                    for i, (sender, timestamp, body) in enumerate(messages, offset + 1)
                ],
            }
        )

    async def send(
        self,
        organization_id: OrganizationID,
        sender: DeviceID,
        recipient: UserID,
        timestamp: DateTime,
        body: bytes,
    ) -> None:
        raise NotImplementedError()

    async def get(
        self, organization_id: OrganizationID, recipient: UserID, offset: int
    ) -> List[Tuple[DeviceID, DateTime, bytes]]:
        raise NotImplementedError()
