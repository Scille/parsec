# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import re
from random import randint, shuffle
from typing import List, Optional, Tuple

from parsec.api.data.base import BaseAPIData, BaseSchema
from parsec.api.data.certif import UserProfile, UserProfileField
from parsec.api.data.entry import EntryID, EntryIDField
from parsec.api.protocol import DeviceID, DeviceIDField, HumanHandle, HumanHandleField
from parsec.crypto import PrivateKey, PublicKey, SecretKey, VerifyKey
from parsec.serde import fields, post_load


class SASCode(str):
    __slots__ = ()
    length = 4
    symbols = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    regex = re.compile(rf"^[{symbols}]{{{length}}}$")

    def __init__(self, raw):
        if not isinstance(raw, str) or not self.regex.match(raw):
            raise ValueError("Invalid SAS code")

    def __repr__(self):
        return f"<OrganizationID {super().__repr__()}>"

    @classmethod
    def from_int(cls, num):
        if num < 0:
            raise ValueError("Provided integer is negative")
        result = ""
        for _ in range(cls.length):
            result += cls.symbols[num % len(cls.symbols)]
            num //= len(cls.symbols)
        if num != 0:
            raise ValueError("Provided integer is too large")
        return cls(result)


def generate_sas_codes(
    claimer_nonce: bytes, greeter_nonce: bytes, shared_secret_key: SecretKey
) -> Tuple[SASCode, SASCode]:
    # Computes combined HMAC
    combined_nonce = claimer_nonce + greeter_nonce
    # Digest size of 5 bytes so we can split it beween two 20bits SAS
    combined_hmac = shared_secret_key.hmac(combined_nonce, digest_size=5)

    hmac_as_int = int.from_bytes(combined_hmac, "big")
    # Big endian number extracted from bits [0, 20[
    claimer_sas = hmac_as_int % 2 ** 20
    # Big endian number extracted from bits [20, 40[
    greeter_sas = (hmac_as_int >> 20) % 2 ** 20

    return SASCode.from_int(claimer_sas), SASCode.from_int(greeter_sas)


def generate_sas_code_candidates(valid_sas: SASCode, size: int = 3) -> List[SASCode]:
    candidates = {valid_sas}
    while len(candidates) < size:
        candidates.add(SASCode.from_int(randint(0, 2 ** 20 - 1)))
    ordered_candidates = list(candidates)
    shuffle(ordered_candidates)
    return ordered_candidates


class InviteUserData(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_user_data", required=True)
        # Claimer ask for device_label/human_handle, but greeter has final word on this
        requested_device_label = fields.String(allow_none=True, missing=None)
        requested_human_handle = HumanHandleField(allow_none=True, missing=None)
        # Note claiming user also imply creating a first device
        public_key = fields.PublicKey(required=True)
        verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteUserData(**data)

    requested_device_label: Optional[str]
    requested_human_handle: Optional[HumanHandle]
    public_key: PublicKey
    verify_key: VerifyKey


class InviteUserConfirmation(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_user_confirmation", required=True)
        device_id = DeviceIDField(required=True)
        device_label = fields.String(allow_none=True, missing=None)
        human_handle = HumanHandleField(allow_none=True, missing=None)
        profile = UserProfileField(required=True)
        root_verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteUserConfirmation(**data)

    device_id: DeviceID
    device_label: Optional[str]
    human_handle: Optional[HumanHandle]
    profile: UserProfile
    root_verify_key: VerifyKey


class InviteDeviceData(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_device_data", required=True)
        # Claimer ask for device_label, but greeter has final word on this
        requested_device_label = fields.String(allow_none=True, missing=None)
        verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteDeviceData(**data)

    requested_device_label: Optional[str]
    verify_key: VerifyKey


class InviteDeviceConfirmation(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_device_confirmation", required=True)
        device_id = DeviceIDField(required=True)
        device_label = fields.String(allow_none=True, missing=None)
        human_handle = HumanHandleField(allow_none=True, missing=None)
        profile = UserProfileField(required=True)
        private_key = fields.PrivateKey(required=True)
        user_manifest_id = EntryIDField(required=True)
        user_manifest_key = fields.SecretKey(required=True)
        root_verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteDeviceConfirmation(**data)

    device_id: DeviceID
    device_label: Optional[str]
    human_handle: Optional[HumanHandle]
    profile: UserProfile
    private_key: PrivateKey
    user_manifest_id: EntryID
    user_manifest_key: SecretKey
    root_verify_key: VerifyKey
