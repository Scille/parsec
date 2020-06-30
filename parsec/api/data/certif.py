# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from enum import Enum
from typing import Optional
from uuid import UUID

from marshmallow import ValidationError

from parsec.api.data.base import BaseAPISignedData, BaseSignedDataSchema, DataValidationError
from parsec.api.protocol import (
    DeviceID,
    DeviceIDField,
    HumanHandle,
    HumanHandleField,
    RealmRole,
    RealmRoleField,
    UserID,
    UserIDField,
)
from parsec.crypto import PublicKey, VerifyKey
from parsec.serde import fields, post_load


class UserProfile(Enum):
    """
    Standard user can create new realms and invite new devices for himself.

    Admin can invite and revoke users and on top of what standard user can do.

    Outsider is only able to collaborate on existing realm and should only
    access redacted certificates (hence he cannot create new realms or
    get OWNER/MANAGER role on a realm)
    """

    ADMIN = "ADMIN"
    STANDARD = "STANDARD"
    OUTSIDER = "OUTSIDER"


UserProfileField = fields.enum_field_factory(UserProfile)


class UserCertificateContent(BaseAPISignedData):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("user_certificate", required=True)
        user_id = UserIDField(required=True)
        # Human handle can be none in case of redacted certificate
        human_handle = HumanHandleField(allow_none=True, missing=None)
        public_key = fields.PublicKey(required=True)
        # `profile` replaces `is_admin` field (which is still required for backward
        # compatibility), hence `None` is not allowed
        is_admin = fields.Boolean(required=True)
        profile = UserProfileField(allow_none=False)

        @post_load
        def make_obj(self, data):
            data.pop("type")

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

            return UserCertificateContent(**data)

    user_id: UserID
    human_handle: Optional[HumanHandle]
    public_key: PublicKey
    profile: UserProfile

    # Only used during schema serialization
    @property
    def is_admin(self) -> bool:
        return self.profile == UserProfile.ADMIN

    @classmethod
    def verify_and_load(
        cls,
        *args,
        expected_user: Optional[UserID] = None,
        expected_human_handle: Optional[HumanHandle] = None,
        **kwargs,
    ) -> "UserCertificateContent":
        data = super().verify_and_load(*args, **kwargs)
        if expected_user is not None and data.user_id != expected_user:
            raise DataValidationError(
                f"Invalid user ID: expected `{expected_user}`, got `{data.user_id}`"
            )
        if expected_human_handle is not None and data.human_handle != expected_human_handle:
            raise DataValidationError(
                f"Invalid human handle: expected `{expected_human_handle}`, got `{data.human_handle}`"
            )
        return data


class RevokedUserCertificateContent(BaseAPISignedData):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("revoked_user_certificate", required=True)
        user_id = UserIDField(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return RevokedUserCertificateContent(**data)

    user_id: UserID

    @classmethod
    def verify_and_load(
        cls, *args, expected_user: Optional[UserID] = None, **kwargs
    ) -> "RevokedUserCertificateContent":
        data = super().verify_and_load(*args, **kwargs)
        if expected_user is not None and data.user_id != expected_user:
            raise DataValidationError(
                f"Invalid user ID: expected `{expected_user}`, got `{data.user_id}`"
            )
        return data


class DeviceCertificateContent(BaseAPISignedData):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("device_certificate", required=True)
        device_id = DeviceIDField(required=True)
        # Device label can be none in case of redacted certificate
        device_label = fields.String(allow_none=True, missing=None)
        verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return DeviceCertificateContent(**data)

    device_id: DeviceID
    device_label: Optional[str]
    verify_key: VerifyKey

    @classmethod
    def verify_and_load(
        cls, *args, expected_device: Optional[DeviceID] = None, **kwargs
    ) -> "DeviceCertificateContent":
        data = super().verify_and_load(*args, **kwargs)
        if expected_device is not None and data.device_id != expected_device:
            raise DataValidationError(
                f"Invalid device ID: expected `{expected_device}`, got `{data.device_id}`"
            )
        return data


class RealmRoleCertificateContent(BaseAPISignedData):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.CheckedConstant("realm_role_certificate", required=True)
        realm_id = fields.UUID(required=True)
        user_id = UserIDField(required=True)
        role = RealmRoleField(required=True, allow_none=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return RealmRoleCertificateContent(**data)

    realm_id: UUID
    user_id: UserID
    role: Optional[RealmRole]  # Set to None if role removed

    @classmethod
    def build_realm_root_certif(cls, author, timestamp, realm_id):
        return cls(
            author=author,
            timestamp=timestamp,
            realm_id=realm_id,
            user_id=author.user_id,
            role=RealmRole.OWNER,
        )

    @classmethod
    def verify_and_load(
        cls,
        *args,
        expected_realm: Optional[UUID] = None,
        expected_user: Optional[UserID] = None,
        expected_role: Optional[RealmRole] = None,
        **kwargs,
    ) -> "RealmRoleCertificateContent":
        data = super().verify_and_load(*args, **kwargs)
        if expected_user is not None and data.user_id != expected_user:
            raise DataValidationError(
                f"Invalid user ID: expected `{expected_user}`, got `{data.user_id}`"
            )
        if expected_realm is not None and data.realm_id != expected_realm:
            raise DataValidationError(
                f"Invalid realm ID: expected `{expected_realm}`, got `{data.realm_id}`"
            )
        if expected_role is not None and data.role != expected_role:
            raise DataValidationError(
                f"Invalid role: expected `{expected_role}`, got `{data.role}`"
            )
        return data
