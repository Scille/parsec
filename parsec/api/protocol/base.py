# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.serde import (
    BaseSchema,
    MsgpackSerializer,
    SerdePackingError,
    SerdeValidationError,
    fields,
)
from parsec.serde import packb as _packb
from parsec.serde import post_load
from parsec.serde import unpackb as _unpackb
from parsec.serde.schema import OneOfSchemaLegacy

__all__ = ("ProtocolError", "BaseReqSchema", "BaseRepSchema", "CmdSerializer")


class ProtocolError(Exception):
    pass


class InvalidMessageError(SerdeValidationError, ProtocolError):
    pass


class MessageSerializationError(SerdePackingError, ProtocolError):
    pass


def packb(data):
    return _packb(data, MessageSerializationError)


def unpackb(data):
    return _unpackb(data, MessageSerializationError)


def serializer_factory(schema_cls):
    return MsgpackSerializer(schema_cls, InvalidMessageError, MessageSerializationError)


class BaseReqSchema(BaseSchema):
    cmd = fields.String(required=True)

    @post_load
    def _drop_cmd_field(self, item):
        if self.drop_cmd_field:
            item.pop("cmd")
        return item

    def __init__(self, drop_cmd_field=True, **kwargs):
        super().__init__(**kwargs)
        self.drop_cmd_field = drop_cmd_field


class BaseRepSchema(BaseSchema):
    status = fields.CheckedConstant("ok", required=True)


class ErrorRepSchema(BaseRepSchema):
    status = fields.String(required=True)
    reason = fields.String(allow_none=True)
    # TODO: should errors be better checked ?
    errors = fields.Dict(allow_none=True)


class CmdSerializer:
    def __repr__(self):
        return (
            f"{self.__class__.__name__}("
            f"req_schema={self._req_serializer}, "
            f"rep_schema={self._rep_serializer})"
        )

    def __init__(self, req_schema_cls, rep_schema_cls):
        self.rep_noerror_schema = rep_schema_cls()

        class RepWithErrorSchema(OneOfSchemaLegacy):
            type_field = "status"
            fallback_type_schema = ErrorRepSchema
            type_schemas = {"ok": self.rep_noerror_schema}

            def get_obj_type(self, obj):
                try:
                    return obj["status"]
                except (TypeError, KeyError):
                    return "ok"

        RepWithErrorSchema.__name__ = f"ErrorOr{rep_schema_cls.__name__}"

        self._req_serializer = serializer_factory(req_schema_cls)
        self._rep_serializer = serializer_factory(RepWithErrorSchema)

        self.req_load = self._req_serializer.load
        self.req_dump = self._req_serializer.dump
        self.rep_load = self._rep_serializer.load
        self.rep_dump = self._rep_serializer.dump
        self.req_loads = self._req_serializer.loads
        self.req_dumps = self._req_serializer.dumps
        self.rep_loads = self._rep_serializer.loads
        self.rep_dumps = self._rep_serializer.dumps
