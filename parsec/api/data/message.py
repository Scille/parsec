# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from enum import Enum

from pendulum import Pendulum

from parsec.api.data.base import BaseAPISignedData, BaseSignedDataSchema
from parsec.api.data.entry import EntryID, EntryIDField
from parsec.crypto import SecretKey
from parsec.serde import OneOfSchema, fields, post_load


class MessageContentType(Enum):
    SHARING_GRANTED = "sharing.granted"
    SHARING_REENCRYPTED = "sharing.reencrypted"
    SHARING_REVOKED = "sharing.revoked"
    PING = "ping"


class MessageContent(BaseAPISignedData):
    class SCHEMA_CLS(OneOfSchema, BaseSignedDataSchema):
        type_field = "type"

        @property
        def type_schemas(self):
            return {
                MessageContentType.SHARING_GRANTED: SharingGrantedMessageContent.SCHEMA_CLS,
                MessageContentType.SHARING_REENCRYPTED: SharingReencryptedMessageContent.SCHEMA_CLS,
                MessageContentType.SHARING_REVOKED: SharingRevokedMessageContent.SCHEMA_CLS,
                MessageContentType.PING: PingMessageContent.SCHEMA_CLS,
            }

        def get_obj_type(self, obj):
            return obj["type"]


class SharingGrantedMessageContent(MessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(MessageContentType.SHARING_GRANTED, required=True)
        name = fields.String(required=True)
        id = EntryIDField(required=True)
        encryption_revision = fields.Integer(required=True)
        encrypted_on = fields.DateTime(required=True)
        key = fields.SecretKey(required=True)
        # Don't include role given the only reliable way to get this information
        # is to fetch the realm role certificate from the backend.
        # Besides, we will also need the message sender's realm role certificate
        # to make sure he is an owner.

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return SharingGrantedMessageContent(**data)

    name: str
    id: EntryID
    encryption_revision: int
    encrypted_on: Pendulum
    key: SecretKey


class SharingReencryptedMessageContent(SharingGrantedMessageContent):
    class SCHEMA_CLS(SharingGrantedMessageContent.SCHEMA_CLS):
        type = fields.EnumCheckedConstant(MessageContentType.SHARING_REENCRYPTED, required=True)
        # This message is similar to `sharing.granted`. Hence both can be processed
        # interchangeably, which avoid possible concurrency issues when a sharing
        # occurs right before a reencryption.

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return SharingReencryptedMessageContent(**data)


class SharingRevokedMessageContent(MessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(MessageContentType.SHARING_REVOKED, required=True)
        id = EntryIDField(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return SharingRevokedMessageContent(**data)

    id: EntryID


class PingMessageContent(MessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(MessageContentType.PING, required=True)
        ping = fields.String(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return PingMessageContent(**data)

    ping: str
