# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pendulum import Pendulum

from parsec.crypto import SecretKey
from parsec.serde import fields, post_load, OneOfSchema
from parsec.api.data.entry import EntryID, EntryIDField
from parsec.api.data.base import BaseAPISignedData, BaseSignedDataSchema


class MessageContent(BaseAPISignedData):
    class SCHEMA_CLS(OneOfSchema, BaseSignedDataSchema):
        type_field = "type"
        type_field_remove = False

        @property
        def type_schemas(self):
            return {
                "sharing.granted": SharingGrantedMessageContent.SCHEMA_CLS,
                "sharing.reencrypted": SharingReencryptedMessageContent.SCHEMA_CLS,
                "sharing.garbage_collected": SharingGarbageCollectedMessageContent.SCHEMA_CLS,
                "sharing.revoked": SharingRevokedMessageContent.SCHEMA_CLS,
                "ping": PingMessageContent.SCHEMA_CLS,
            }

        def get_obj_type(self, obj):
            return obj["type"]


class SharingGrantedMessageContent(MessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("sharing.granted", required=True)
        name = fields.String(required=True)
        id = EntryIDField(required=True)
        encryption_revision = fields.Integer(required=True)
        encrypted_on = fields.DateTime(required=True)
        key = fields.SecretKey(required=True)
        garbage_collection_revision = fields.Integer(required=True)
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
    garbage_collection_revision: int


class SharingReencryptedMessageContent(SharingGrantedMessageContent):
    class SCHEMA_CLS(SharingGrantedMessageContent.SCHEMA_CLS):
        type = fields.CheckedConstant("sharing.reencrypted", required=True)
        # This message is similar to `sharing.granted`. Hence both can be processed
        # interchangeably, which avoid possible concurrency issues when a sharing
        # occurs right before a reencryption.

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return SharingReencryptedMessageContent(**data)


class SharingRevokedMessageContent(MessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("sharing.revoked", required=True)
        id = EntryIDField(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return SharingRevokedMessageContent(**data)

    id: EntryID


class SharingGarbageCollectedMessageContent(MessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("sharing.garbage_collected", required=True)
        id = EntryIDField(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return SharingGarbageCollectedMessageContent(**data)

    id: EntryID


class PingMessageContent(MessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("ping", required=True)
        ping = fields.String(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return PingMessageContent(**data)

    ping: str
