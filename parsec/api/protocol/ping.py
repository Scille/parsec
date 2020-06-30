# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.protocol.base import BaseRepSchema, BaseReqSchema, CmdSerializer
from parsec.serde import fields

__all__ = ("ping_serializer",)


class PingReqSchema(BaseReqSchema):
    ping = fields.String(required=True)


class PingRepSchema(BaseRepSchema):
    pong = fields.String(required=True)


ping_serializer = CmdSerializer(PingReqSchema, PingRepSchema)
