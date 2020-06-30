# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.protocol.base import BaseRepSchema, BaseReqSchema, CmdSerializer
from parsec.api.protocol.types import OrganizationIDField
from parsec.serde import fields

__all__ = "organization_bootstrap_serializer"


class APIV1_OrganizationCreateReqSchema(BaseReqSchema):
    organization_id = OrganizationIDField(required=True)
    expiration_date = fields.DateTime(allow_none=True, required=False)


class APIV1_OrganizationCreateRepSchema(BaseRepSchema):
    bootstrap_token = fields.String(required=True)
    expiration_date = fields.DateTime(allow_none=True, required=False)


apiv1_organization_create_serializer = CmdSerializer(
    APIV1_OrganizationCreateReqSchema, APIV1_OrganizationCreateRepSchema
)


class APIV1_OrganizationBootstrapReqSchema(BaseReqSchema):
    bootstrap_token = fields.String(required=True)
    root_verify_key = fields.VerifyKey(required=True)
    user_certificate = fields.Bytes(required=True)
    device_certificate = fields.Bytes(required=True)
    # Same certificates than above, but expurged of human_handle/device_label
    # Backward compatibility prevent those field to be required, however
    # they should be considered so by recent version of Parsec (hence the
    # `allow_none=False`).
    # Hence only old version of Parsec will provide a payload with missing
    # redacted fields. In such case we consider the non-redacted can also
    # be used as redacted given the to-be-redacted fields have been introduce
    # in later version of Parsec.
    redacted_user_certificate = fields.Bytes(allow_none=False)
    redacted_device_certificate = fields.Bytes(allow_none=False)
    root_verify_key = fields.VerifyKey(required=True)


class APIV1_OrganizationBootstrapRepSchema(BaseRepSchema):
    pass


# TODO: remove me once API v1 is deprecated
apiv1_organization_bootstrap_serializer = CmdSerializer(
    APIV1_OrganizationBootstrapReqSchema, APIV1_OrganizationBootstrapRepSchema
)


class APIV1_OrganizationStatsReqSchema(BaseReqSchema):
    organization_id = OrganizationIDField(required=True)


class APIV1_OrganizationStatsRepSchema(BaseRepSchema):
    data_size = fields.Integer(required=True)
    metadata_size = fields.Integer(required=True)
    users = fields.Integer(required=True)


apiv1_organization_stats_serializer = CmdSerializer(
    APIV1_OrganizationStatsReqSchema, APIV1_OrganizationStatsRepSchema
)


class APIV1_OrganizationStatusReqSchema(BaseReqSchema):
    organization_id = OrganizationIDField(required=True)


class APIV1_OrganizationStatusRepSchema(BaseRepSchema):
    is_bootstrapped = fields.Boolean(required=True)
    expiration_date = fields.DateTime(allow_none=True, required=False)


apiv1_organization_status_serializer = CmdSerializer(
    APIV1_OrganizationStatusReqSchema, APIV1_OrganizationStatusRepSchema
)


class APIV1_OrganizationUpdateReqSchema(BaseReqSchema):
    organization_id = OrganizationIDField(required=True)
    expiration_date = fields.DateTime(allow_none=True, required=False)


class APIV1_OrganizationUpdateRepSchema(BaseRepSchema):
    pass


apiv1_organization_update_serializer = CmdSerializer(
    APIV1_OrganizationUpdateReqSchema, APIV1_OrganizationUpdateRepSchema
)
