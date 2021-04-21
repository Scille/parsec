# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import attr
from typing import Optional, Any, TypeVar, Type
from parsec.datetime import DateTime

from parsec.serde import (
    BaseSchema,
    fields,
    SerdeValidationError,
    SerdePackingError,
    BaseSerializer,
    ZipMsgpackSerializer,
)
from parsec.crypto import CryptoError, PrivateKey, PublicKey, SigningKey, VerifyKey, SecretKey
from parsec.api.protocol import DeviceID, DeviceIDField


class DataError(Exception):
    pass


class DataValidationError(SerdeValidationError, DataError):
    pass


class DataSerializationError(SerdePackingError, DataError):
    pass


class BaseSignedDataSchema(BaseSchema):
    author = DeviceIDField(required=True, allow_none=False)
    timestamp = fields.DateTime(required=True)


class DataMeta(type):

    BASE_SCHEMA_CLS = BaseSchema
    CLS_ATTR_COOKING = attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)

    def __new__(cls, name, bases, nmspc):
        # Sanity checks
        if "SCHEMA_CLS" not in nmspc:
            raise RuntimeError("Missing attribute `SCHEMA_CLS` in class definition")
        if not issubclass(nmspc["SCHEMA_CLS"], cls.BASE_SCHEMA_CLS):
            raise RuntimeError(f"Attribute `SCHEMA_CLS` must inherit {BaseSignedDataSchema!r}")

        raw_cls = type.__new__(cls, name, bases, nmspc)

        # Sanity checks: class SCHEMA_CLS needs to define parents SCHEMA_CLS fields
        schema_cls_fields = set(nmspc["SCHEMA_CLS"]._declared_fields)
        bases_schema_cls = (base for base in bases if hasattr(base, "SCHEMA_CLS"))
        for base in bases_schema_cls:
            assert base.SCHEMA_CLS._declared_fields.keys() <= schema_cls_fields

        # Sanity check: attr fields need to be defined in SCHEMA_CLS
        if "__attrs_attrs__" in nmspc:
            assert {att.name for att in nmspc["__attrs_attrs__"]} <= schema_cls_fields
        try:
            serializer_cls = raw_cls.SERIALIZER_CLS
        except AttributeError:
            raise RuntimeError("Missing attribute `SERIALIZER_CLS` in class definition")

        if not issubclass(serializer_cls, BaseSerializer):
            raise RuntimeError(f"Attribute `SERIALIZER_CLS` must inherit {BaseSerializer!r}")

        raw_cls.SERIALIZER = serializer_cls(
            nmspc["SCHEMA_CLS"], DataValidationError, DataSerializationError
        )

        return raw_cls


class SignedDataMeta(DataMeta):
    BASE_SCHEMA_CLS = BaseSignedDataSchema


BaseSignedDataTypeVar = TypeVar("BaseSignedDataTypeVar", bound="BaseSignedData")


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class BaseSignedData(metaclass=SignedDataMeta):
    """
    Most data within the api should inherit this class. The goal is to have
    immutable data (thanks to attr frozen) that can be easily (de)serialize
    with encryption/signature support.
    """

    # Must be overloaded by child classes
    SCHEMA_CLS = BaseSignedDataSchema
    SERIALIZER_CLS = BaseSerializer

    author: DeviceID
    timestamp: DateTime

    def __eq__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return attr.astuple(self).__eq__(attr.astuple(other))
        return NotImplemented

    def evolve(self: BaseSignedDataTypeVar, **kwargs: object) -> BaseSignedDataTypeVar:
        return attr.evolve(self, **kwargs)

    def _serialize(self) -> bytes:
        """
        Raises:
            DataError
        """
        return self.SERIALIZER.dumps(self)  # type: ignore[attr-defined]

    @classmethod
    def _deserialize(cls: Type[BaseSignedDataTypeVar], raw: bytes) -> BaseSignedDataTypeVar:
        """
        Raises:
            DataError
        """
        try:
            return cls.SERIALIZER.loads(raw)  # type: ignore[attr-defined]
        except DataError:
            raise

    def dump_and_sign(self, author_signkey: SigningKey) -> bytes:
        """
        Raises:
            DataError
        """
        try:
            return author_signkey.sign(self._serialize())

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

    def dump_sign_and_encrypt(self, author_signkey: SigningKey, key: SecretKey) -> bytes:
        """
        Raises:
            DataError
        """
        try:
            signed = author_signkey.sign(self._serialize())
            return key.encrypt(signed)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

    def dump_sign_and_encrypt_for(
        self, author_signkey: SigningKey, recipient_pubkey: PublicKey
    ) -> bytes:
        """
        Raises:
            DataError
        """
        try:
            signed = author_signkey.sign(self._serialize())
            return recipient_pubkey.encrypt_for_self(signed)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

    @classmethod
    def unsecure_load(cls: Type[BaseSignedDataTypeVar], signed: bytes) -> BaseSignedDataTypeVar:
        """
        Raises:
            DataError
        """
        raw = VerifyKey.unsecure_unwrap(signed)
        return cls._deserialize(raw)

    @classmethod
    def verify_and_load(
        cls: Type[BaseSignedDataTypeVar],
        signed: bytes,
        author_verify_key: VerifyKey,
        expected_author: Optional[DeviceID],
        expected_timestamp: Optional[DateTime] = None,
        **kwargs,
    ) -> BaseSignedDataTypeVar:
        """
        Raises:
            DataError
        """
        try:
            raw = author_verify_key.verify(signed)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        data = cls._deserialize(raw)
        if data.author != expected_author:
            repr_expected_author = expected_author or "<root key>"
            raise DataError(
                f"Invalid author: expected `{repr_expected_author}`, got `{data.author}`"
            )
        if expected_timestamp is not None and data.timestamp != expected_timestamp:
            raise DataError(
                f"Invalid timestamp: expected `{expected_timestamp}`, got `{data.timestamp}`"
            )
        return data

    @classmethod
    def decrypt_verify_and_load(
        cls: Type[BaseSignedDataTypeVar],
        encrypted: bytes,
        key: SecretKey,
        author_verify_key: VerifyKey,
        expected_author: DeviceID,
        expected_timestamp: DateTime,
        **kwargs,
    ) -> BaseSignedDataTypeVar:
        """
        Raises:
            DataError
        """
        try:
            signed = key.decrypt(encrypted)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        return cls.verify_and_load(
            signed,
            author_verify_key=author_verify_key,
            expected_author=expected_author,
            expected_timestamp=expected_timestamp,
            **kwargs,
        )

    @classmethod
    def decrypt_verify_and_load_for(
        cls: Type[BaseSignedDataTypeVar],
        encrypted: bytes,
        recipient_privkey: PrivateKey,
        author_verify_key: VerifyKey,
        expected_author: DeviceID,
        expected_timestamp: DateTime,
        **kwargs,
    ) -> BaseSignedDataTypeVar:
        """
        Raises:
            DataError
        """
        try:
            signed = recipient_privkey.decrypt_from_self(encrypted)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        return cls.verify_and_load(
            signed,
            author_verify_key=author_verify_key,
            expected_author=expected_author,
            expected_timestamp=expected_timestamp,
            **kwargs,
        )


BaseDataTypeVar = TypeVar("BaseDataTypeVar", bound="BaseData")


class BaseData(metaclass=DataMeta):
    """
    Some data within the api don't have to be signed (e.g. claim info) and
    should inherit this class.
    The goal is to have immutable data (thanks to attr frozen) that can be
    easily (de)serialize with encryption support.
    """

    # Must be overloaded by child classes
    SCHEMA_CLS = BaseSchema
    SERIALIZER_CLS = BaseSerializer

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, type(self)):
            return attr.astuple(self).__eq__(attr.astuple(other))
        return NotImplemented

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)

    def dump(self) -> bytes:
        """
        Raises:
            DataError
        """
        return self.SERIALIZER.dumps(self)  # type: ignore[attr-defined]

    @classmethod
    def load(cls: Type[BaseDataTypeVar], raw: bytes, **kwargs: object) -> BaseDataTypeVar:
        """
        Raises:
            DataError
        """
        return cls.SERIALIZER.loads(raw)  # type: ignore[attr-defined]

    def dump_and_encrypt(self, key: SecretKey) -> bytes:
        """
        Raises:
            DataError
        """
        try:
            raw = self.dump()
            return key.encrypt(raw)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

    def dump_and_encrypt_for(self, recipient_pubkey: PublicKey) -> bytes:
        """
        Raises:
            DataError
        """
        try:
            raw = self.dump()
            return recipient_pubkey.encrypt_for_self(raw)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

    @classmethod
    def decrypt_and_load(
        cls: Type[BaseDataTypeVar], encrypted: bytes, key: SecretKey, **kwargs: object
    ) -> BaseDataTypeVar:
        """
        Raises:
            DataError
        """
        try:
            raw = key.decrypt(encrypted)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        return cls.load(raw, **kwargs)

    @classmethod
    def decrypt_and_load_for(
        cls: Type[BaseDataTypeVar],
        encrypted: bytes,
        recipient_privkey: PrivateKey,
        **kwargs: object,
    ) -> BaseDataTypeVar:
        """
        Raises:
            DataError
        """
        try:
            raw = recipient_privkey.decrypt_from_self(encrypted)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        return cls.load(raw, **kwargs)


# Data class with serializers


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class BaseAPISignedData(BaseSignedData):
    """Signed and compressed base class for API data"""

    SCHEMA_CLS = BaseSignedDataSchema
    SERIALIZER_CLS = ZipMsgpackSerializer


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class BaseAPIData(BaseData):
    """Unsigned and compressed base class for API data"""

    SCHEMA_CLS = BaseSchema
    SERIALIZER_CLS = ZipMsgpackSerializer
