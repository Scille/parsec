# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.protocol.base import BaseRepSchema, BaseReqSchema, CmdSerializer
from parsec.api.protocol.types import DeviceIDField, DeviceNameField, HumanHandleField, UserIDField
from parsec.serde import BaseSchema, fields

__all__ = (
    "user_get_serializer",
    "apiv1_user_find_serializer",
    "apiv1_user_invite_serializer",
    "apiv1_user_get_invitation_creator_serializer",
    "apiv1_user_claim_serializer",
    "apiv1_user_cancel_invitation_serializer",
    "user_create_serializer",
    "user_revoke_serializer",
    "apiv1_device_invite_serializer",
    "apiv1_device_get_invitation_creator_serializer",
    "apiv1_device_claim_serializer",
    "apiv1_device_cancel_invitation_serializer",
    "device_create_serializer",
    "human_find_serializer",
)


#### Access user API ####


class TrustchainSchema(BaseSchema):
    devices = fields.List(fields.Bytes(required=True))
    users = fields.List(fields.Bytes(required=True))
    revoked_users = fields.List(fields.Bytes(required=True))


class UserGetReqSchema(BaseReqSchema):
    user_id = UserIDField(required=True)


class UserGetRepSchema(BaseRepSchema):
    user_certificate = fields.Bytes(required=True)
    revoked_user_certificate = fields.Bytes(required=True, allow_none=True)
    device_certificates = fields.List(fields.Bytes(required=True), required=True)

    trustchain = fields.Nested(TrustchainSchema, required=True)


user_get_serializer = CmdSerializer(UserGetReqSchema, UserGetRepSchema)


class APIV1_UserFindReqSchema(BaseReqSchema):
    query = fields.String(missing=None)
    omit_revoked = fields.Boolean(missing=False)
    page = fields.Int(missing=1, validate=lambda n: n > 0)
    per_page = fields.Integer(missing=100, validate=lambda n: 0 < n <= 100)


class APIV1_UserFindRepSchema(BaseRepSchema):
    results = fields.List(UserIDField())
    page = fields.Int(validate=lambda n: n > 0)
    per_page = fields.Integer(validate=lambda n: 0 < n <= 100)
    total = fields.Int(validate=lambda n: n >= 0)


# TODO: remove me once API v1 is deprecated
apiv1_user_find_serializer = CmdSerializer(APIV1_UserFindReqSchema, APIV1_UserFindRepSchema)


#### User creation API ####


class APIV1_UserInviteReqSchema(BaseReqSchema):
    user_id = UserIDField(required=True)


class APIV1_UserInviteRepSchema(BaseRepSchema):
    encrypted_claim = fields.Bytes(required=True)


# TODO: remove me once API v1 is deprecated
apiv1_user_invite_serializer = CmdSerializer(APIV1_UserInviteReqSchema, APIV1_UserInviteRepSchema)


class APIV1_UserGetInvitationCreatorReqSchema(BaseReqSchema):
    invited_user_id = UserIDField(required=True)


class APIV1_UserGetInvitationCreatorRepSchema(BaseRepSchema):
    device_certificate = fields.Bytes(required=True)
    user_certificate = fields.Bytes(required=True)
    trustchain = fields.Nested(TrustchainSchema, required=True)


# TODO: remove me once API v1 is deprecated
apiv1_user_get_invitation_creator_serializer = CmdSerializer(
    APIV1_UserGetInvitationCreatorReqSchema, APIV1_UserGetInvitationCreatorRepSchema
)


class APIV1_UserClaimReqSchema(BaseReqSchema):
    invited_user_id = UserIDField(required=True)
    encrypted_claim = fields.Bytes(required=True)


class APIV1_UserClaimRepSchema(BaseRepSchema):
    user_certificate = fields.Bytes(required=True)
    device_certificate = fields.Bytes(required=True)


# TODO: remove me once API v1 is deprecated
apiv1_user_claim_serializer = CmdSerializer(APIV1_UserClaimReqSchema, APIV1_UserClaimRepSchema)


class APIV1_UserCancelInvitationReqSchema(BaseReqSchema):
    user_id = UserIDField(required=True)


class APIV1_UserCancelInvitationRepSchema(BaseRepSchema):
    pass


# TODO: remove me once API v1 is deprecated
apiv1_user_cancel_invitation_serializer = CmdSerializer(
    APIV1_UserCancelInvitationReqSchema, APIV1_UserCancelInvitationRepSchema
)


class APIV1_UserCreateReqSchema(BaseReqSchema):
    user_certificate = fields.Bytes(required=True)
    device_certificate = fields.Bytes(required=True)


class APIV1_UserCreateRepSchema(BaseRepSchema):
    pass


apiv1_user_create_serializer = CmdSerializer(APIV1_UserCreateReqSchema, APIV1_UserCreateRepSchema)


class UserCreateReqSchema(BaseReqSchema):
    user_certificate = fields.Bytes(required=True)
    device_certificate = fields.Bytes(required=True)
    # Same certificates than above, but expurged of human_handle/device_label
    redacted_user_certificate = fields.Bytes(required=True)
    redacted_device_certificate = fields.Bytes(required=True)


class UserCreateRepSchema(BaseRepSchema):
    pass


user_create_serializer = CmdSerializer(UserCreateReqSchema, UserCreateRepSchema)


class UserRevokeReqSchema(BaseReqSchema):
    revoked_user_certificate = fields.Bytes(required=True)


class UserRevokeRepSchema(BaseRepSchema):
    pass


user_revoke_serializer = CmdSerializer(UserRevokeReqSchema, UserRevokeRepSchema)


#### Device creation API ####


class APIV1_DeviceInviteReqSchema(BaseReqSchema):
    invited_device_name = DeviceNameField(required=True)


class APIV1_DeviceInviteRepSchema(BaseRepSchema):
    encrypted_claim = fields.Bytes(required=True)


# TODO: remove me once API v1 is deprecated
apiv1_device_invite_serializer = CmdSerializer(
    APIV1_DeviceInviteReqSchema, APIV1_DeviceInviteRepSchema
)


class APIV1_DeviceGetInvitationCreatorReqSchema(BaseReqSchema):
    invited_device_id = DeviceIDField(required=True)


class APIV1_DeviceGetInvitationCreatorRepSchema(BaseRepSchema):
    device_certificate = fields.Bytes(required=True)
    user_certificate = fields.Bytes(required=True)
    trustchain = fields.Nested(TrustchainSchema, required=True)


# TODO: remove me once API v1 is deprecated
apiv1_device_get_invitation_creator_serializer = CmdSerializer(
    APIV1_DeviceGetInvitationCreatorReqSchema, APIV1_DeviceGetInvitationCreatorRepSchema
)


class APIV1_DeviceClaimReqSchema(BaseReqSchema):
    invited_device_id = DeviceIDField(required=True)
    encrypted_claim = fields.Bytes(required=True)


class APIV1_DeviceClaimRepSchema(BaseRepSchema):
    device_certificate = fields.Bytes(required=True)
    encrypted_answer = fields.Bytes(required=True)


# TODO: remove me once API v1 is deprecated
apiv1_device_claim_serializer = CmdSerializer(
    APIV1_DeviceClaimReqSchema, APIV1_DeviceClaimRepSchema
)


class APIV1_DeviceCancelInvitationReqSchema(BaseReqSchema):
    invited_device_name = DeviceNameField(required=True)


class APIV1_DeviceCancelInvitationRepSchema(BaseRepSchema):
    pass


# TODO: remove me once API v1 is deprecated
apiv1_device_cancel_invitation_serializer = CmdSerializer(
    APIV1_DeviceCancelInvitationReqSchema, APIV1_DeviceCancelInvitationRepSchema
)


class APIV1_DeviceCreateReqSchema(BaseReqSchema):
    device_certificate = fields.Bytes(required=True)
    encrypted_answer = fields.Bytes(required=True)


class APIV1_DeviceCreateRepSchema(BaseRepSchema):
    pass


apiv1_device_create_serializer = CmdSerializer(
    APIV1_DeviceCreateReqSchema, APIV1_DeviceCreateRepSchema
)


class DeviceCreateReqSchema(BaseReqSchema):
    device_certificate = fields.Bytes(required=True)
    # Same certificate than above, but expurged of device_label
    redacted_device_certificate = fields.Bytes(required=True)


class DeviceCreateRepSchema(BaseRepSchema):
    pass


device_create_serializer = CmdSerializer(DeviceCreateReqSchema, DeviceCreateRepSchema)


# Human search API


class HumanFindReqSchema(BaseReqSchema):
    query = fields.String(missing=None)
    omit_revoked = fields.Boolean(missing=False)
    omit_non_human = fields.Boolean(missing=False)
    page = fields.Int(missing=1, validate=lambda n: n > 0)
    per_page = fields.Integer(missing=100, validate=lambda n: 0 < n <= 100)


class HumanFindResultItemSchema(BaseSchema):
    user_id = UserIDField(required=True)
    human_handle = HumanHandleField(allow_none=True, missing=None)
    revoked = fields.Boolean(required=True)


class HumanFindRepSchema(BaseRepSchema):
    results = fields.List(fields.Nested(HumanFindResultItemSchema, required=True))
    page = fields.Int(validate=lambda n: n > 0)
    per_page = fields.Integer(validate=lambda n: 0 < n <= 100)
    total = fields.Int(validate=lambda n: n >= 0)


human_find_serializer = CmdSerializer(HumanFindReqSchema, HumanFindRepSchema)
