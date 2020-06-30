# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from enum import Enum
from secrets import token_bytes
from typing import List, Optional, Tuple
from uuid import UUID

from parsec.api.protocol.base import InvalidMessageError, ProtocolError, serializer_factory
from parsec.api.protocol.invite import InvitationType, InvitationTypeField
from parsec.api.protocol.types import DeviceID, DeviceIDField, OrganizationID, OrganizationIDField
from parsec.api.version import API_V1_VERSION, API_V2_VERSION, ApiVersion
from parsec.crypto import CryptoError, SigningKey, VerifyKey
from parsec.serde import BaseSchema, OneOfSchema, fields, validate


class HandshakeError(ProtocolError):
    pass


class HandshakeFailedChallenge(HandshakeError):
    pass


class HandshakeBadAdministrationToken(HandshakeError):
    pass


class HandshakeBadIdentity(HandshakeError):
    pass


class HandshakeOrganizationExpired(HandshakeError):
    pass


class HandshakeRVKMismatch(HandshakeError):
    pass


class HandshakeRevokedDevice(HandshakeError):
    pass


class HandshakeAPIVersionError(HandshakeError):
    def __init__(
        self, backend_versions: List[ApiVersion], client_versions: List[ApiVersion] = None
    ):
        self.client_versions = client_versions
        self.backend_versions = backend_versions
        client_versions_str = "{" + ", ".join(map(str, client_versions)) + "}"
        backend_versions_str = "{" + ", ".join(map(str, backend_versions)) + "}"
        self.message = (
            f"No overlap between client API versions {client_versions_str} "
            f"and backend API versions {backend_versions_str}"
        )

    def __str__(self):
        return self.message


class HandshakeType(Enum):
    AUTHENTICATED = "AUTHENTICATED"
    INVITED = "INVITED"


HandshakeTypeField = fields.enum_field_factory(HandshakeType)


class APIV1_HandshakeType(Enum):
    AUTHENTICATED = "authenticated"
    ANONYMOUS = "anonymous"
    ADMINISTRATION = "administration"


APIV1_HandshakeTypeField = fields.enum_field_factory(APIV1_HandshakeType)


def _settle_compatible_versions(
    backend_versions: List[ApiVersion], client_versions: List[ApiVersion]
) -> Tuple[ApiVersion, ApiVersion]:
    # Try to use the newest version first
    for cv in reversed(sorted(client_versions)):
        # No need to compare `revision` because only `version` field breaks compatibility
        bv = next((bv for bv in backend_versions if bv.version == cv.version), None)
        if bv:
            return bv, cv
    raise HandshakeAPIVersionError(backend_versions, client_versions)


class ApiVersionField(fields.Tuple):
    def __init__(self, **kwargs):
        version = fields.Integer(required=True, validate=validate.Range(min=0))
        revision = fields.Integer(required=True, validate=validate.Range(min=0))
        super().__init__(version, revision, **kwargs)

    def _deserialize(self, *args, **kwargs):
        result = super()._deserialize(*args, **kwargs)
        return ApiVersion(*result)


class HandshakeChallengeSchema(BaseSchema):
    handshake = fields.CheckedConstant("challenge", required=True)
    challenge = fields.Bytes(required=True)
    supported_api_versions = fields.List(ApiVersionField(), required=True)


handshake_challenge_serializer = serializer_factory(HandshakeChallengeSchema)


class HandshakeAnswerVersionOnlySchema(BaseSchema):
    handshake = fields.CheckedConstant("answer", required=True)
    client_api_version = ApiVersionField(required=True)


handshake_answer_version_only_serializer = serializer_factory(HandshakeAnswerVersionOnlySchema)


class HandshakeAuthenticatedAnswerSchema(BaseSchema):
    handshake = fields.CheckedConstant("answer", required=True)
    type = fields.EnumCheckedConstant(HandshakeType.AUTHENTICATED, required=True)
    client_api_version = ApiVersionField(required=True)
    organization_id = OrganizationIDField(required=True)
    device_id = DeviceIDField(required=True)
    rvk = fields.VerifyKey(required=True)
    answer = fields.Bytes(required=True)


class HandshakeInvitedAnswerSchema(BaseSchema):
    handshake = fields.CheckedConstant("answer", required=True)
    type = fields.EnumCheckedConstant(HandshakeType.INVITED, required=True)
    client_api_version = ApiVersionField(required=True)
    organization_id = OrganizationIDField(required=True)
    invitation_type = InvitationTypeField(required=True)
    token = fields.UUID(required=True)


class HandshakeAnswerSchema(OneOfSchema):
    type_field = "type"
    type_schemas = {
        HandshakeType.AUTHENTICATED: HandshakeAuthenticatedAnswerSchema(),
        HandshakeType.INVITED: HandshakeInvitedAnswerSchema(),
    }

    def get_obj_type(self, obj):
        return obj["type"]


handshake_answer_serializer = serializer_factory(HandshakeAnswerSchema)


class APIV1_HandshakeAuthenticatedAnswerSchema(BaseSchema):
    handshake = fields.CheckedConstant("answer", required=True)
    type = fields.EnumCheckedConstant(APIV1_HandshakeType.AUTHENTICATED, required=True)
    client_api_version = ApiVersionField(required=True)
    organization_id = OrganizationIDField(required=True)
    device_id = DeviceIDField(required=True)
    rvk = fields.VerifyKey(required=True)
    answer = fields.Bytes(required=True)


class APIV1_HandshakeAnonymousAnswerSchema(BaseSchema):
    handshake = fields.CheckedConstant("answer", required=True)
    type = fields.EnumCheckedConstant(APIV1_HandshakeType.ANONYMOUS, required=True)
    client_api_version = ApiVersionField(required=True)
    organization_id = OrganizationIDField(required=True)
    # Cannot provide rvk during organization bootstrap
    rvk = fields.VerifyKey(missing=None)


class APIV1_HandshakeAdministrationAnswerSchema(BaseSchema):
    handshake = fields.CheckedConstant("answer", required=True)
    type = fields.EnumCheckedConstant(APIV1_HandshakeType.ADMINISTRATION, required=True)
    client_api_version = ApiVersionField(required=True)
    token = fields.String(required=True)


class APIV1_HandshakeAnswerSchema(OneOfSchema):
    type_field = "type"
    type_schemas = {
        APIV1_HandshakeType.AUTHENTICATED: APIV1_HandshakeAuthenticatedAnswerSchema(),
        APIV1_HandshakeType.ANONYMOUS: APIV1_HandshakeAnonymousAnswerSchema(),
        APIV1_HandshakeType.ADMINISTRATION: APIV1_HandshakeAdministrationAnswerSchema(),
    }

    def get_obj_type(self, obj):
        return obj["type"]


apiv1_handshake_answer_serializer = serializer_factory(APIV1_HandshakeAnswerSchema)


class HandshakeResultSchema(BaseSchema):
    handshake = fields.CheckedConstant("result", required=True)
    result = fields.String(required=True)
    help = fields.String(missing=None)


handshake_result_serializer = serializer_factory(HandshakeResultSchema)


class ServerHandshake:
    # Class attribute
    SUPPORTED_API_VERSIONS = (API_V2_VERSION, API_V1_VERSION)

    def __init__(self, challenge_size: int = 48):
        # Challenge
        self.challenge_size = challenge_size
        self.challenge = None
        self.answer_type = None
        self.answer_data = None

        # API version
        self.client_api_version = None
        self.backend_api_version = None

        # State
        self.state = "stalled"

    def is_anonymous(self):
        return self.device_id is None

    def build_challenge_req(self) -> bytes:
        if not self.state == "stalled":
            raise HandshakeError("Invalid state.")

        self.challenge = token_bytes(self.challenge_size)
        self.state = "challenge"

        return handshake_challenge_serializer.dumps(
            {
                "handshake": "challenge",
                "challenge": self.challenge,
                "supported_api_versions": self.SUPPORTED_API_VERSIONS,
            }
        )

    def process_answer_req(self, req: bytes):
        if not self.state == "challenge":
            raise HandshakeError("Invalid state.")

        # Retrieve the version used by client first
        data = handshake_answer_version_only_serializer.loads(req)
        client_api_version = data["client_api_version"]
        if client_api_version.version == 2:
            serializer = handshake_answer_serializer
        elif client_api_version.version == 1:
            serializer = apiv1_handshake_answer_serializer
        else:
            # Imcompatible version !
            raise HandshakeAPIVersionError(self.SUPPORTED_API_VERSIONS, [client_api_version])

        # Now we know how to deserialize the rest of the data
        data = serializer.loads(req)

        data.pop("handshake")
        self.answer_type = data.pop("type")
        self.answer_data = data
        self.state = "answer"

        # API version matching
        self.backend_api_version, self.client_api_version = _settle_compatible_versions(
            self.SUPPORTED_API_VERSIONS, [client_api_version]
        )

    def build_bad_protocol_result_req(self, help="Invalid params") -> bytes:
        if self.state not in ("answer", "challenge"):
            raise HandshakeError("Invalid state.")

        self.state = "result"
        return handshake_result_serializer.dumps(
            {"handshake": "result", "result": "bad_protocol", "help": help}
        )

    def build_bad_administration_token_result_req(
        self, help="Invalid administration token"
    ) -> bytes:
        if not self.state == "answer":
            raise HandshakeError("Invalid state.")

        self.state = "result"
        return handshake_result_serializer.dumps(
            {"handshake": "result", "result": "bad_admin_token", "help": help}
        )

    def build_bad_identity_result_req(self, help="Invalid handshake information") -> bytes:
        """
        We should keep the help for this result voluntarily broad otherwise
        an attacker could use it to brute force informations.
        """
        if not self.state == "answer":
            raise HandshakeError("Invalid state.")

        self.state = "result"
        return handshake_result_serializer.dumps(
            {"handshake": "result", "result": "bad_identity", "help": help}
        )

    def build_organization_expired_result_req(self, help="Trial organization has expired") -> bytes:
        if not self.state == "answer":
            raise HandshakeError("Invalid state.")

        self.state = "result"
        return handshake_result_serializer.dumps(
            {"handshake": "result", "result": "organization_expired", "help": help}
        )

    def build_rvk_mismatch_result_req(self, help=None) -> bytes:
        if not self.state == "answer":
            raise HandshakeError("Invalid state.")

        help = help or ("Root verify key for organization differs between client and server")

        self.state = "result"
        return handshake_result_serializer.dumps(
            {"handshake": "result", "result": "rvk_mismatch", "help": help}
        )

    def build_revoked_device_result_req(self, help="Device has been revoked") -> bytes:
        if not self.state == "answer":
            raise HandshakeError("Invalid state.")

        self.state = "result"
        return handshake_result_serializer.dumps(
            {"handshake": "result", "result": "revoked_device", "help": help}
        )

    def build_result_req(self, verify_key=None) -> bytes:
        if not self.state == "answer":
            raise HandshakeError("Invalid state.")

        if self.answer_type in (HandshakeType.AUTHENTICATED, APIV1_HandshakeType.AUTHENTICATED):
            if not verify_key:
                raise HandshakeError(
                    "`verify_key` param must be provided for authenticated handshake"
                )

            try:
                returned_challenge = verify_key.verify(self.answer_data["answer"])
                if returned_challenge != self.challenge:
                    raise HandshakeFailedChallenge("Invalid returned challenge")

            except CryptoError as exc:
                raise HandshakeFailedChallenge("Invalid answer signature") from exc

        self.state = "result"
        return handshake_result_serializer.dumps({"handshake": "result", "result": "ok"})


class BaseClientHandshake:
    SUPPORTED_API_VERSIONS = None  # Overwritten by subclasses

    def __init__(self):
        self.challenge_data = None
        self.backend_api_version = None
        self.client_api_version = None

    def load_challenge_req(self, req: bytes):
        self.challenge_data = handshake_challenge_serializer.loads(req)

        # API version matching
        self.backend_api_version, self.client_api_version = _settle_compatible_versions(
            self.challenge_data["supported_api_versions"], self.SUPPORTED_API_VERSIONS
        )

    def process_result_req(self, req: bytes) -> bytes:
        data = handshake_result_serializer.loads(req)
        if data["result"] != "ok":
            if data["result"] == "bad_identity":
                raise HandshakeBadIdentity(data["help"])

            if data["result"] == "organization_expired":
                raise HandshakeOrganizationExpired(data["help"])

            elif data["result"] == "rvk_mismatch":
                raise HandshakeRVKMismatch(data["help"])

            elif data["result"] == "revoked_device":
                raise HandshakeRevokedDevice(data["help"])

            if data["result"] == "bad_admin_token":
                raise HandshakeBadAdministrationToken(data["help"])

            else:
                raise InvalidMessageError(
                    f"Bad `result` handshake: {data['result']} ({data['help']})"
                )


class AuthenticatedClientHandshake(BaseClientHandshake):
    SUPPORTED_API_VERSIONS = (API_V2_VERSION,)
    HANDSHAKE_TYPE = HandshakeType.AUTHENTICATED
    HANDSHAKE_ANSWER_SERIALIZER = handshake_answer_serializer

    def __init__(
        self,
        organization_id: OrganizationID,
        device_id: DeviceID,
        user_signkey: SigningKey,
        root_verify_key: VerifyKey,
    ):
        self.organization_id = organization_id
        self.device_id = device_id
        self.user_signkey = user_signkey
        self.root_verify_key = root_verify_key

    def process_challenge_req(self, req: bytes) -> bytes:
        self.load_challenge_req(req)
        answer = self.user_signkey.sign(self.challenge_data["challenge"])
        return self.HANDSHAKE_ANSWER_SERIALIZER.dumps(
            {
                "handshake": "answer",
                "type": self.HANDSHAKE_TYPE,
                "client_api_version": self.client_api_version,
                "organization_id": self.organization_id,
                "device_id": self.device_id,
                "rvk": self.root_verify_key,
                "answer": answer,
            }
        )


class APIV1_AuthenticatedClientHandshake(AuthenticatedClientHandshake):
    SUPPORTED_API_VERSIONS = (API_V1_VERSION,)
    HANDSHAKE_TYPE = APIV1_HandshakeType.AUTHENTICATED
    HANDSHAKE_ANSWER_SERIALIZER = apiv1_handshake_answer_serializer


class InvitedClientHandshake(BaseClientHandshake):
    SUPPORTED_API_VERSIONS = (API_V2_VERSION,)

    def __init__(
        self, organization_id: OrganizationID, invitation_type: InvitationType, token: UUID
    ):
        self.organization_id = organization_id
        self.invitation_type = invitation_type
        self.token = token

    def process_challenge_req(self, req: bytes) -> bytes:
        self.load_challenge_req(req)
        return handshake_answer_serializer.dumps(
            {
                "handshake": "answer",
                "type": HandshakeType.INVITED,
                "client_api_version": self.client_api_version,
                "organization_id": self.organization_id,
                "invitation_type": self.invitation_type,
                "token": self.token,
            }
        )


class APIV1_AnonymousClientHandshake(BaseClientHandshake):
    SUPPORTED_API_VERSIONS = (API_V1_VERSION,)

    def __init__(
        self, organization_id: OrganizationID, root_verify_key: Optional[VerifyKey] = None
    ):
        self.organization_id = organization_id
        self.root_verify_key = root_verify_key

    def process_challenge_req(self, req: bytes) -> bytes:
        self.load_challenge_req(req)
        return apiv1_handshake_answer_serializer.dumps(
            {
                "handshake": "answer",
                "type": APIV1_HandshakeType.ANONYMOUS,
                "client_api_version": self.client_api_version,
                "organization_id": self.organization_id,
                "rvk": self.root_verify_key,
            }
        )


class APIV1_AdministrationClientHandshake(BaseClientHandshake):
    SUPPORTED_API_VERSIONS = (API_V1_VERSION,)

    def __init__(self, token: str):
        self.token = token

    def process_challenge_req(self, req: bytes) -> bytes:
        self.load_challenge_req(req)
        return apiv1_handshake_answer_serializer.dumps(
            {
                "handshake": "answer",
                "type": APIV1_HandshakeType.ADMINISTRATION,
                "client_api_version": self.client_api_version,
                "token": self.token,
            }
        )
