# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from enum import Enum
from typing import Dict, Union

from marshmallow import MarshalResult, Schema, UnmarshalResult, ValidationError, post_load

from parsec.serde.fields import String

try:
    import toastedmarshmallow

    class BaseSchema(Schema):
        class Meta:
            jit = toastedmarshmallow.Jit


except ImportError:
    BaseSchema = Schema


class BaseCmdSchema(BaseSchema):

    cmd = String(required=True)

    @post_load
    def _drop_cmd_field(self, item):
        if self.drop_cmd_field:
            item.pop("cmd")
        return item

    def __init__(self, drop_cmd_field=True, **kwargs):
        super().__init__(**kwargs)
        self.drop_cmd_field = drop_cmd_field


# Shamelessly taken from marshmallow-oneofschema (
# https://github.com/maximkulkin/marshmallow-oneofschema - MIT licensed)
# This is needed because marshmallow-oneofschema depends of marshmallow which
# cannot be installed along with toastedmarshmallow
class OneOfSchema(BaseSchema):
    """
    This is a special kind of schema that actually multiplexes other schemas
    based on object type. When serializing values, it uses get_obj_type() method
    to get object type name. Then it uses `type_schemas` name-to-Schema mapping
    to get schema for that particular object type, serializes object using that
    schema and adds an extra "type" field with name of object type.
    Deserialization is reverse.

    Example:

        class Foo(object):
            def __init__(self, foo):
                self.foo = foo

        class Bar(object):
            def __init__(self, bar):
                self.bar = bar

        class FooSchema(marshmallow.Schema):
            foo = marshmallow.fields.String(required=True)

            @marshmallow.post_load
            def make_foo(self, data):
                return Foo(**data)

        class BarSchema(marshmallow.Schema):
            bar = marshmallow.fields.Integer(required=True)

            @marshmallow.post_load
            def make_bar(self, data):
                return Bar(**data)

        class MyUberSchema(marshmallow.OneOfSchema):
            type_schemas = {
                'foo': FooSchema,
                'bar': BarSchema,
            }

            def get_obj_type(self, obj):
                if isinstance(obj, Foo):
                    return 'foo'
                elif isinstance(obj, Bar):
                    return 'bar'
                else:
                    raise Exception('Unknown object type: %s' % repr(obj))

        MyUberSchema().dump([Foo(foo='hello'), Bar(bar=123)], many=True).data
        # => [{'type': 'foo', 'foo': 'hello'}, {'type': 'bar', 'bar': 123}]

    You can control type field name added to serialized object representation by
    setting `type_field` class property.
    """

    type_field: str = "type"
    type_schemas: Dict[Enum, Schema] = {}
    fallback_type_schema: Schema = None
    _instantiated_schemas: Dict[Union[Enum, str], Schema] = None
    _instantiated_fallback_schema: Schema = None

    def _build_schemas(self):
        enum_types = {type(k) for k in self.type_schemas.keys()}
        if len(enum_types) != 1 or not issubclass(enum_types.pop(), Enum):
            raise ValueError("type_schemas key can only be one Enum")
        self._instantiated_schemas = {}
        for k, v in self.type_schemas.items():
            schema_instance = v if isinstance(v, Schema) else v()
            if self.type_field not in schema_instance.fields:
                raise ValueError(f"{schema_instance} needs to define '{self.type_field}' field")
            self._instantiated_schemas[k] = schema_instance
            self._instantiated_schemas[k.value] = schema_instance

        if self.fallback_type_schema:
            self._instantiated_fallback_schema = self.fallback_type_schema()

    def _get_schema(self, type: Union[Enum, str]):
        if self._instantiated_schemas is None:
            self._build_schemas()
        return self._instantiated_schemas.get(type, self._instantiated_fallback_schema)

    def get_obj_type(self, obj):
        """Returns name of object schema"""
        raise NotImplementedError()

    def dump(self, obj, many=None, update_fields=True, **kwargs):
        many = self.many if many is None else bool(many)
        if not many:
            result = self._dump(obj, update_fields, **kwargs)
        else:
            result_data = []
            result_errors = {}

            for idx, o in enumerate(obj):
                result = self._dump(o, update_fields, **kwargs)
                result_data.append(result.data)
                if result.errors:
                    result_errors[idx] = result.errors

            result = MarshalResult(result_data, result_errors)

        if result.errors and self.strict:
            raise ValidationError(result.errors)

        return result

    def _dump(self, obj, update_fields=True, **kwargs):
        obj_type = self.get_obj_type(obj)
        if not obj_type:
            return MarshalResult(
                None, {"_schema": "Unknown object class: %s" % obj.__class__.__name__}
            )

        schema = self._get_schema(obj_type)
        if not schema:
            return MarshalResult(None, {"_schema": "Unsupported object type: %s" % obj_type})

        result = schema.dump(obj, many=False, update_fields=update_fields, **kwargs)
        return result

    def load(self, data, many=None, partial=None):
        many = self.many if many is None else bool(many)
        if partial is None:
            partial = self.partial

        if not many:
            result = self._load(data, partial=partial)
        else:
            result_data = []
            result_errors = {}

            for idx, item in enumerate(data):
                result = self._load(item, partial=partial)
                result_data.append(result.data)
                if result.errors:
                    result_errors[idx] = result.errors

            result = UnmarshalResult(result_data, result_errors)

        if result.errors and self.strict:
            raise ValidationError(result.errors)

        return result

    def _load(self, data, partial=None):
        if not isinstance(data, dict):
            return UnmarshalResult({}, {"_schema": "Invalid data type: %s" % data})

        data = dict(data)

        data_type = data.get(self.type_field)

        if not data_type:
            return UnmarshalResult({}, {self.type_field: ["Missing data for required field."]})

        try:
            schema = self._get_schema(data_type)
        except TypeError:
            # data_type could be unhashable
            return UnmarshalResult({}, {self.type_field: ["Invalid value: %s" % data_type]})

        if not schema:
            return UnmarshalResult({}, {self.type_field: ["Unsupported value: %s" % data_type]})

        return schema.load(data, many=False, partial=partial)

    def validate(self, data, many=None, partial=None):
        try:
            return self.load(data, many=many, partial=partial).errors
        except ValidationError as ve:
            return ve.messages


class OneOfSchemaLegacy(OneOfSchema):
    _instantiated_schemas: Dict[str, Schema] = None
    _instantiated_fallback_schema: Schema = None

    def _get_schema(self, type):
        if self._instantiated_schemas is None:
            self._instantiated_schemas = {
                k: v if isinstance(v, Schema) else v() for k, v in self.type_schemas.items()
            }

            if self.fallback_type_schema:
                self._instantiated_fallback_schema = self.fallback_type_schema()

        return self._instantiated_schemas.get(type, self._instantiated_fallback_schema)
