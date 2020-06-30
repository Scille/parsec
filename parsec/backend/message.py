# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Tuple

from pendulum import Pendulum

from parsec.api.protocol import DeviceID, OrganizationID, UserID, message_get_serializer
from parsec.backend.utils import api, catch_protocol_errors


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
        timestamp: Pendulum,
        body: bytes,
    ) -> None:
        raise NotImplementedError()

    async def get(
        self, organization_id: OrganizationID, recipient: UserID, offset: int
    ) -> List[Tuple[DeviceID, Pendulum, bytes]]:
        raise NotImplementedError()
