# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import attr
from typing import Tuple, Optional
from hashlib import sha256
from marshmallow import ValidationError
from pendulum import DateTime, now as pendulum_now

from parsec.crypto import SecretKey, PrivateKey, SigningKey
from parsec.serde import fields, post_load
from parsec.api.protocol import (
    UserID,
    DeviceID,
    OrganizationID,
    HumanHandle,
    DeviceIDField,
    HumanHandleField,
)
from parsec.api.data import BaseSchema, EntryID, EntryIDField, UserProfile, UserProfileField
from parsec.core.types.base import BaseLocalData
from parsec.core.types.backend_address import BackendOrganizationAddr, BackendOrganizationAddrField


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class LocalDevice(BaseLocalData):
    class SCHEMA_CLS(BaseSchema):
        organization_addr = BackendOrganizationAddrField(required=True)
        device_id = DeviceIDField(required=True)
        device_label = fields.String(allow_none=True, missing=None)
        human_handle = HumanHandleField(allow_none=True, missing=None)
        signing_key = fields.SigningKey(required=True)
        private_key = fields.PrivateKey(required=True)
        # `profile` replaces `is_admin` field (which is still required for backward
        # compatibility), hence `None` is not allowed
        is_admin = fields.Boolean(required=True)
        profile = UserProfileField(allow_none=False)
        user_manifest_id = EntryIDField(required=True)
        user_manifest_key = fields.SecretKey(required=True)
        local_symkey = fields.SecretKey(required=True)

        @post_load
        def make_obj(self, data):
            # Handle legacy `is_admin` field
            default_profile = UserProfile.ADMIN if data.pop("is_admin") else UserProfile.STANDARD
            try:
                profile = data["profile"]
            except KeyError:
                data["profile"] = default_profile
            else:
                if default_profile == UserProfile.ADMIN and profile != UserProfile.ADMIN:
                    raise ValidationError(
                        "Fields `profile` and `is_admin` have incompatible values"
                    )

            return LocalDevice(**data)

    organization_addr: BackendOrganizationAddr
    device_id: DeviceID
    device_label: Optional[str]
    human_handle: Optional[HumanHandle]
    signing_key: SigningKey
    private_key: PrivateKey
    profile: UserProfile
    user_manifest_id: EntryID
    user_manifest_key: SecretKey
    local_symkey: SecretKey

    # Only used during schema serialization
    @property
    def is_admin(self) -> bool:
        return self.profile == UserProfile.ADMIN

    @property
    def is_outsider(self) -> bool:
        return self.profile == UserProfile.OUTSIDER

    def __repr__(self):
        return f"{self.__class__.__name__}({self.device_id})"

    @property
    def slug(self) -> str:
        """The slug is unique identifier for a particular device.

        It is composed of a small part of the RVK hash, the organization ID
        and the device ID, although it shouldn't be assumed that this information
        can be recovered from the slug as this might change in the future.

        The purpose of the slog is simply to tell whether `LocalDevice` and
        `AvailableDevice` objects corresponds to the same device.
        """
        # Add a hash to avoid clash when the backend is reseted
        # and we recreate a device with same organization/device_id
        # organization and device_id than a previous one
        hash_part = sha256(self.root_verify_key.encode()).hexdigest()[:10]
        return f"{hash_part}#{self.organization_id}#{self.device_id}"

    @property
    def slughash(self) -> str:
        """
        Slug is long and not readable enough (given device_id is made of uuids).
        Hence it's often simpler to rely on it hash instead (e.g. select the
        device to use in the CLI by providing the beginning of the hash)
        """
        return sha256(self.slug.encode()).hexdigest()

    @staticmethod
    def load_slug(slug: str) -> Tuple[OrganizationID, DeviceID]:
        """
        Raises: ValueError
        """
        _, raw_org_id, raw_device_id = slug.split("#")
        return OrganizationID(raw_org_id), DeviceID(raw_device_id)

    @property
    def root_verify_key(self):
        return self.organization_addr.root_verify_key

    @property
    def organization_id(self):
        return self.organization_addr.organization_id

    @property
    def device_name(self):
        return self.device_id.device_name

    @property
    def user_id(self):
        return self.device_id.user_id

    @property
    def verify_key(self):
        return self.signing_key.verify_key

    @property
    def public_key(self):
        return self.private_key.public_key

    @property
    def user_display(self) -> str:
        return str(self.human_handle or self.user_id)

    @property
    def short_user_display(self) -> str:
        return str(self.human_handle.label if self.human_handle else self.user_id)

    @property
    def device_display(self) -> str:
        return str(self.device_label or self.device_id.device_name)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserInfo:
    user_id: UserID
    human_handle: Optional[HumanHandle]
    profile: UserProfile
    created_on: DateTime
    revoked_on: Optional[DateTime]

    @property
    def user_display(self) -> str:
        return str(self.human_handle or self.user_id)

    @property
    def short_user_display(self) -> str:
        return str(self.human_handle.label if self.human_handle else self.user_id)

    @property
    def is_revoked(self):
        return pendulum_now() >= self.revoked_on if self.revoked_on else False

    def __repr__(self):
        return f"<UserInfo {self.user_display}>"


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceInfo:
    device_id: DeviceID
    device_label: Optional[str]
    created_on: DateTime

    @property
    def device_name(self):
        return self.device_id.device_name

    @property
    def device_display(self) -> str:
        return str(self.device_label or self.device_id.device_name)

    def __repr__(self):
        return f"DeviceInfo({self.device_display})"
