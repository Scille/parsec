# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from base64 import b32decode, b32encode
from hashlib import sha256
from typing import Tuple

from nacl.bindings import crypto_scalarmult, crypto_sign_BYTES
from nacl.encoding import RawEncoder
from nacl.exceptions import CryptoError  # noqa: republishing
from nacl.hash import BLAKE2B_BYTES, blake2b
from nacl.public import PrivateKey as _PrivateKey
from nacl.public import PublicKey as _PublicKey
from nacl.public import SealedBox
from nacl.pwhash import argon2i
from nacl.secret import SecretBox
from nacl.signing import SigningKey as _SigningKey
from nacl.signing import VerifyKey as _VerifyKey
from nacl.utils import random

# Note to simplify things, we adopt `nacl.CryptoError` as our root error cls


__all__ = (
    # Exceptions
    "CryptoError",
    # Types
    "SecretKey",
    "HashDigest",
    "PrivateKey",
    "PublicKey",
    "SigningKey",
    "VerifyKey",
    # Helpers
    "export_root_verify_key",
    "import_root_verify_key",
    "derivate_secret_key_from_password",
)


# TODO: SENSITIVE is really slow which is not good for unittests...
# CRYPTO_OPSLIMIT = argon2i.OPSLIMIT_SENSITIVE
# CRYPTO_MEMLIMIT = argon2i.MEMLIMIT_SENSITIVE
CRYPTO_OPSLIMIT = argon2i.OPSLIMIT_INTERACTIVE
CRYPTO_MEMLIMIT = argon2i.MEMLIMIT_INTERACTIVE


# Types


class SecretKey(bytes):
    __slots__ = ()

    @classmethod
    def generate(cls) -> "SecretKey":
        return cls(random(SecretBox.KEY_SIZE))

    def __repr__(self):
        # Avoid leaking the key in logs
        return f"<{type(self).__module__}.{type(self).__qualname__} object at {hex(id(self))}>"

    @classmethod
    def from_password(cls, password: str) -> "SecretKey":
        pass

    def encrypt(self, data: bytes) -> bytes:
        """
        Raises:
            CryptoError: if key is invalid.
        """
        box = SecretBox(self)
        return box.encrypt(data)

    def decrypt(self, ciphered: bytes) -> bytes:
        """
        Raises:
            CryptoError: if key is invalid.
        """
        box = SecretBox(self)
        return box.decrypt(ciphered)

    def hmac(self, data: bytes, digest_size=BLAKE2B_BYTES) -> bytes:
        return blake2b(data, digest_size=digest_size, key=self, encoder=RawEncoder)


class HashDigest(bytes):
    __slots__ = ()

    @classmethod
    def from_data(self, data: bytes) -> "HashDigest":
        # nacl's sha256 doesn't accept bytearray, so stick to `hashlib.sha256`
        return HashDigest(sha256(data).digest())


# Basically just add comparison support to nacl keys


class SigningKey(_SigningKey):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verify_key.__class__ = VerifyKey

    @classmethod
    def generate(cls, *args, **kwargs) -> "SigningKey":
        obj = super().generate(*args, **kwargs)
        obj.__class__ = SigningKey
        return obj

    def __eq__(self, other):
        return isinstance(other, _SigningKey) and self._signing_key == other._signing_key


class VerifyKey(_VerifyKey):
    __slots__ = ()

    def __eq__(self, other):
        return isinstance(other, _VerifyKey) and self._key == other._key

    @classmethod
    def unsecure_unwrap(self, signed: bytes) -> bytes:
        return signed[crypto_sign_BYTES:]


class PrivateKey(_PrivateKey):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.public_key.__class__ = PublicKey

    @classmethod
    def generate(cls, *args, **kwargs) -> "PrivateKey":
        obj = super().generate(*args, **kwargs)
        obj.__class__ = PrivateKey
        return obj

    def __eq__(self, other):
        return isinstance(other, _PrivateKey) and self._private_key == other._private_key

    def decrypt_from_self(self, ciphered: bytes) -> bytes:
        """
        raises:
            CryptoError
        """
        return SealedBox(self).decrypt(ciphered)


class PublicKey(_PublicKey):
    __slots__ = ()

    def __eq__(self, other):
        return isinstance(other, _PublicKey) and self._public_key == other._public_key

    def encrypt_for_self(self, data: bytes) -> bytes:
        """
        raises:
            CryptoError
        """
        return SealedBox(self).encrypt(data)


# Helpers


def export_root_verify_key(key: VerifyKey) -> str:
    """
    Raises:
        ValueError
    """
    # Note we replace padding char `=` by a simple `s` (which is not part of
    # the base32 table so no risk of collision) to avoid copy/paste errors
    # and silly escaping issues when carrying the key around.
    return b32encode(key.encode()).decode("utf8").replace("=", "s")


def import_root_verify_key(raw: str) -> VerifyKey:
    """
    Raises:
        ValueError
    """
    if isinstance(raw, VerifyKey):
        # Useful during tests
        return raw
    try:
        return VerifyKey(b32decode(raw.replace("s", "=").encode("utf8")))
    except CryptoError as exc:
        raise ValueError("Invalid verify key") from exc


def derivate_secret_key_from_password(password: str, salt: bytes = None) -> Tuple[SecretKey, bytes]:
    salt = salt or random(argon2i.SALTBYTES)
    rawkey = argon2i.kdf(
        SecretBox.KEY_SIZE,
        password.encode("utf8"),
        salt,
        opslimit=CRYPTO_OPSLIMIT,
        memlimit=CRYPTO_MEMLIMIT,
    )
    return SecretKey(rawkey), salt


def generate_shared_secret_key(
    our_private_key: PrivateKey, peer_public_key: PublicKey
) -> SecretKey:
    return SecretKey(crypto_scalarmult(our_private_key.encode(), peer_public_key.encode()))


def generate_nonce(size=64) -> bytes:
    return random(size=size)
