# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional

import attr
from pendulum import Pendulum

from parsec.api.protocol import DeviceID, DeviceIDField
from parsec.crypto import CryptoError, PrivateKey, PublicKey, SecretKey, SigningKey, VerifyKey
from parsec.serde import (
    BaseSchema,
    BaseSerializer,
    SerdePackingError,
    SerdeValidationError,
    ZipMsgpackSerializer,
    fields,
)


class DataError(Exception):
    pass


class DataValidationError(SerdeValidationError, DataError):
    pass


class DataSerializationError(SerdePackingError, DataError):
    pass


class BaseSignedDataSchema(BaseSchema):
    author = DeviceIDField(required=True, allow_none=True)
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

        # During the creation of a class, we wrap it with `attr.s`.
        # Under the hood, attr recreate a class so this metaclass is going to
        # be called a second time.
        # We must detect this second call to avoid infinie loop.
        if "__attrs_attrs__" in nmspc:
            return type.__new__(cls, name, bases, nmspc)

        if "SERIALIZER" in nmspc:
            raise RuntimeError("Attribute `SERIALIZER` is reserved")

        raw_cls = type.__new__(cls, name, bases, nmspc)

        try:
            serializer_cls = raw_cls.SERIALIZER_CLS
        except AttributeError:
            raise RuntimeError("Missing attribute `SERIALIZER_CLS` in class definition")

        if not issubclass(serializer_cls, BaseSerializer):
            raise RuntimeError(f"Attribute `SERIALIZER_CLS` must inherit {BaseSerializer!r}")

        raw_cls.SERIALIZER = serializer_cls(
            nmspc["SCHEMA_CLS"], DataValidationError, DataSerializationError
        )

        return cls.CLS_ATTR_COOKING(raw_cls)


class SignedDataMeta(DataMeta):
    BASE_SCHEMA_CLS = BaseSignedDataSchema


class BaseSignedData(metaclass=SignedDataMeta):
    """
    Most data within the api should inherit this class. The goal is to have
    immutable data (thanks to attr frozen) that can be easily (de)serialize
    with encryption/signature support.
    """

    # Must be overloaded by child classes
    SCHEMA_CLS = BaseSignedDataSchema
    SERIALIZER_CLS = BaseSerializer

    author: Optional[DeviceID]  # Set to None if signed by the root key
    timestamp: Pendulum

    def __eq__(self, other: "BaseSignedData") -> bool:
        if isinstance(other, type(self)):
            return attr.astuple(self).__eq__(attr.astuple(other))
        return NotImplemented

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)

    def _serialize(self) -> bytes:
        """
        Raises:
            DataError
        """
        return self.SERIALIZER.dumps(self)

    @classmethod
    def _deserialize(cls, raw: bytes) -> "BaseSignedData":
        """
        Raises:
            DataError
        """
        try:
            return cls.SERIALIZER.loads(raw)
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

    def dump_sign_and_encrypt(self, author_signkey: SigningKey, key: bytes) -> bytes:
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
    def unsecure_load(self, signed: bytes) -> "BaseSignedData":
        """
        Raises:
            DataError
        """
        raw = VerifyKey.unsecure_unwrap(signed)
        return self._deserialize(raw)

    @classmethod
    def verify_and_load(
        cls,
        signed: bytes,
        author_verify_key: VerifyKey,
        expected_author: Optional[DeviceID],
        expected_timestamp: Pendulum = None,
    ) -> "BaseSignedData":
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
        self,
        encrypted: bytes,
        key: bytes,
        author_verify_key: VerifyKey,
        expected_author: DeviceID,
        expected_timestamp: Pendulum,
        **kwargs,
    ) -> "BaseSignedData":
        """
        Raises:
            DataError
        """
        try:
            signed = key.decrypt(encrypted)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        return self.verify_and_load(
            signed,
            author_verify_key=author_verify_key,
            expected_author=expected_author,
            expected_timestamp=expected_timestamp,
            **kwargs,
        )

    @classmethod
    def decrypt_verify_and_load_for(
        self,
        encrypted: bytes,
        recipient_privkey: PrivateKey,
        author_verify_key: VerifyKey,
        expected_author: DeviceID,
        expected_timestamp: Pendulum,
        **kwargs,
    ) -> "BaseSignedData":
        """
        Raises:
            DataError
        """
        try:
            signed = recipient_privkey.decrypt_from_self(encrypted)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        return self.verify_and_load(
            signed,
            author_verify_key=author_verify_key,
            expected_author=expected_author,
            expected_timestamp=expected_timestamp,
            **kwargs,
        )


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

    def __eq__(self, other: "BaseData") -> bool:
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
        return self.SERIALIZER.dumps(self)

    @classmethod
    def load(cls, raw: bytes) -> "BaseData":
        """
        Raises:
            DataError
        """
        return cls.SERIALIZER.loads(raw)

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
    def decrypt_and_load(cls, encrypted: bytes, key: SecretKey, **kwargs) -> "BaseData":
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
        self, encrypted: bytes, recipient_privkey: PrivateKey, **kwargs
    ) -> "BaseData":
        """
        Raises:
            DataError
        """
        try:
            raw = recipient_privkey.decrypt_from_self(encrypted)

        except CryptoError as exc:
            raise DataError(str(exc)) from exc

        return self.load(raw, **kwargs)


# Data class with serializers


class BaseAPISignedData(BaseSignedData):
    """Signed and compressed base class for API data"""

    SCHEMA_CLS = BaseSignedDataSchema
    SERIALIZER_CLS = ZipMsgpackSerializer


class BaseAPIData(BaseData):
    """Unsigned and compressed base class for API data"""

    SCHEMA_CLS = BaseSchema
    SERIALIZER_CLS = ZipMsgpackSerializer
