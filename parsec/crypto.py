# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os

from typing import Tuple
from base64 import b32decode, b32encode
from hashlib import sha256
from ctypes import cdll, c_size_t, c_uint8, byref, cast, POINTER

from nacl.exceptions import CryptoError  # noqa: republishing
from nacl.public import SealedBox, PrivateKey as _PrivateKey, PublicKey as _PublicKey
from nacl.signing import SigningKey as _SigningKey, VerifyKey as _VerifyKey
from nacl.bindings import crypto_sign_BYTES, crypto_scalarmult
from nacl.hash import blake2b, BLAKE2B_BYTES
from nacl.pwhash import argon2i
from nacl.utils import random
from nacl.encoding import RawEncoder

# from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
# from cryptography.exceptions import InvalidTag

# Note to simplify things, we adopt `nacl.CryptoError` as our root error cls

LibSgx = cdll.LoadLibrary(os.path.dirname(__file__) + "/sgxlib.so")
LibSgx.initialize_enclave()

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

SGX_AESGCM_MAC_SIZE = 16
SGX_AESGCM_IV_SIZE = 12
# Types


class SecretKey(bytes):
    __slots__ = ()

    @classmethod
    def generate(cls) -> "SecretKey":
        return cls(os.urandom(16))

    def __repr__(self):
        # Avoid leaking the key in logs
        return f"<{type(self).__module__}.{type(self).__qualname__} object at {hex(id(self))}>"

    # Using AES with SGX enclave
    def encrypt(self, data):
        ecall_encryptMessage = LibSgx.ecall_encryptMessage
        ecall_encryptMessage.restype = POINTER(c_uint8)
        encMessageLen = c_size_t()
        encrypted_ptr = ecall_encryptMessage(self, data, len(data), byref(encMessageLen))
        encrypted_data = cast(encrypted_ptr, POINTER(c_uint8 * encMessageLen.value))
        encrypted_data = bytes(list(encrypted_data.contents))
        # print("ici")
        # print()
        # print()
        # print("encrypted data = ", encrypted_data)

        return encrypted_data

    def decrypt(self, data):
        data_len = len(data)
        output_len = data_len - SGX_AESGCM_MAC_SIZE - SGX_AESGCM_IV_SIZE
        ecall_decryptMessage = LibSgx.ecall_decryptMessage
        ecall_decryptMessage.restype = POINTER(c_uint8 * output_len)
        decrypted_data = bytes(list(ecall_decryptMessage(self, data, data_len).contents))
        # print("la")
        # print()
        # print()
        # print("decrypted data = ", decrypted_data)
        return decrypted_data

    # Using AES WITHOUT ENCLAVE
    # def encrypt(self, data):
    #     iv = os.urandom(16)
    #     cipher = Cipher(algorithms.AES(self), modes.GCM(iv))
    #     encryptor = cipher.encryptor()
    #     ciphered = encryptor.update(data) + encryptor.finalize()
    #     tag = encryptor.tag
    #     token = iv + tag + ciphered
    #     return token
    # def decrypt(self, token):
    #     iv = token[0:16]
    #     tag = token[16:32]
    #     ciphered = token[32:]
    #     try:
    #         cipher = Cipher(algorithms.AES(self), modes.GCM(iv, tag))
    #         decryptor = cipher.decryptor()
    #         decrypted_message = decryptor.update(ciphered) + decryptor.finalize()
    #         print()
    #         print()
    #         print("decrypted_message = ", decrypted_message)
    #         return decrypted_message
    #     except (InvalidTag, ValueError) as exc:
    #         raise CryptoError(str(exc)) from exc

    # Using pyNaCl
    # def encrypt(self, data: bytes) -> bytes:
    #     """
    #     Raises:
    #         CryptoError: if key is invalid.
    #     """
    #     box = SecretBox(self)
    #     return box.encrypt(data)

    # def decrypt(self, ciphered: bytes) -> bytes:
    #     """
    #     Raises:
    #         CryptoError: if key is invalid.
    #     """
    #     box = SecretBox(self)
    #     return box.decrypt(ciphered)

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
        32, password.encode("utf8"), salt, opslimit=CRYPTO_OPSLIMIT, memlimit=CRYPTO_MEMLIMIT
    )
    return SecretKey(rawkey), salt


def generate_shared_secret_key(
    our_private_key: PrivateKey, peer_public_key: PublicKey
) -> SecretKey:
    return SecretKey(crypto_scalarmult(our_private_key.encode(), peer_public_key.encode()))


def generate_nonce(size=64) -> bytes:
    return random(size=size)
