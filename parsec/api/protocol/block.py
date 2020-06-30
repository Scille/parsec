# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.protocol.base import BaseRepSchema, BaseReqSchema, CmdSerializer
from parsec.serde import fields

__all__ = ("block_create_serializer", "block_read_serializer")


class BlockCreateReqSchema(BaseReqSchema):
    block_id = fields.UUID(required=True)
    realm_id = fields.UUID(required=True)
    block = fields.Bytes(required=True)


class BlockCreateRepSchema(BaseRepSchema):
    pass


block_create_serializer = CmdSerializer(BlockCreateReqSchema, BlockCreateRepSchema)


class BlockReadReqSchema(BaseReqSchema):
    block_id = fields.UUID(required=True)


class BlockReadRepSchema(BaseRepSchema):
    block = fields.Bytes(required=True)


block_read_serializer = CmdSerializer(BlockReadReqSchema, BlockReadRepSchema)
