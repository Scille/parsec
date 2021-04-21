# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.datetime import DateTime
from typing import Dict, Type, TypeVar, Any

from enum import Enum
import attr
from parsec.crypto import SecretKey
from parsec.serde import fields, post_load, OneOfSchema
from parsec.api.data.entry import EntryID, EntryIDField, EntryNameField, EntryName
from parsec.api.data.base import BaseAPISignedData, BaseSignedDataSchema


class MessageContentType(Enum):
    SHARING_GRANTED = "sharing.granted"
    SHARING_REENCRYPTED = "sharing.reencrypted"
    SHARING_REVOKED = "sharing.revoked"
    PING = "ping"


BaseSignedDataSchemaTypeVar = TypeVar("BaseSignedDataSchemaTypeVar", bound="BaseSignedDataSchema")
BaseMessageContentSchemaTyping = Dict[MessageContentType, BaseSignedDataSchemaTypeVar]


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class BaseMessageContent(BaseAPISignedData):
    class SCHEMA_CLS(OneOfSchema, BaseSignedDataSchema):
        type_field = "type"

        @property
        def type_schemas(  # type: ignore[override]
            self
        ) -> Dict[MessageContentType, Type[OneOfSchema]]:
            return {
                MessageContentType.SHARING_GRANTED: SharingGrantedMessageContent.SCHEMA_CLS,
                MessageContentType.SHARING_REENCRYPTED: SharingReencryptedMessageContent.SCHEMA_CLS,
                MessageContentType.SHARING_REVOKED: SharingRevokedMessageContent.SCHEMA_CLS,
                MessageContentType.PING: PingMessageContent.SCHEMA_CLS,
            }

        def get_obj_type(self, obj: Dict[str, object]) -> object:
            return obj["type"]


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class SharingGrantedMessageContent(BaseMessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(MessageContentType.SHARING_GRANTED, required=True)
        name = EntryNameField(required=True)
        id = EntryIDField(required=True)
        encryption_revision = fields.Integer(required=True)
        encrypted_on = fields.DateTime(required=True)
        key = fields.SecretKey(required=True)
        # Don't include role given the only reliable way to get this information
        # is to fetch the realm role certificate from the backend.
        # Besides, we will also need the message sender's realm role certificate
        # to make sure he is an owner.

        @post_load
        def make_obj(  # type: ignore[misc]
            self, data: Dict[str, Any]
        ) -> "SharingGrantedMessageContent":
            data.pop("type")
            return SharingGrantedMessageContent(**data)

    name: EntryName
    id: EntryID
    encryption_revision: int
    encrypted_on: DateTime
    key: SecretKey


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class SharingReencryptedMessageContent(SharingGrantedMessageContent):
    class SCHEMA_CLS(SharingGrantedMessageContent.SCHEMA_CLS):
        type = fields.EnumCheckedConstant(MessageContentType.SHARING_REENCRYPTED, required=True)
        # This message is similar to `sharing.granted`. Hence both can be processed
        # interchangeably, which avoid possible concurrency issues when a sharing
        # occurs right before a reencryption.

        @post_load
        def make_obj(  # type: ignore[misc]
            self, data: Dict[str, Any]
        ) -> "SharingReencryptedMessageContent":
            data.pop("type")
            return SharingReencryptedMessageContent(**data)


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class SharingRevokedMessageContent(BaseMessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(MessageContentType.SHARING_REVOKED, required=True)
        id = EntryIDField(required=True)

        @post_load
        def make_obj(  # type: ignore[misc]
            self, data: Dict[str, Any]
        ) -> "SharingRevokedMessageContent":
            data.pop("type")
            return SharingRevokedMessageContent(**data)

    id: EntryID


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class PingMessageContent(BaseMessageContent):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(MessageContentType.PING, required=True)
        ping = fields.String(required=True)

        @post_load
        def make_obj(self, data: Dict[str, Any]) -> "PingMessageContent":  # type: ignore[misc]
            data.pop("type")
            return PingMessageContent(**data)

    ping: str
