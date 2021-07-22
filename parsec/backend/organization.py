# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import attr
import pendulum
from typing import Optional, Union, List
from secrets import token_hex

from pendulum import DateTime

from parsec.utils import timestamps_in_the_ballpark
from parsec.crypto import VerifyKey
from parsec.api.protocol import (
    OrganizationID,
    HandshakeType,
    organization_stats_serializer,
    APIV1_HandshakeType,
    apiv1_organization_create_serializer,
    apiv1_organization_bootstrap_serializer,
    apiv1_organization_stats_serializer,
    apiv1_organization_status_serializer,
    apiv1_organization_update_serializer,
    organization_config_serializer,
)
from parsec.api.data import UserCertificateContent, DeviceCertificateContent, DataError, UserProfile
from parsec.backend.user import User, Device
from parsec.backend.webhooks import WebhooksComponent
from parsec.backend.utils import catch_protocol_errors, api, Unset, UnsetType
from parsec.backend.config import BackendConfig


class OrganizationError(Exception):
    pass


class OrganizationAlreadyExistsError(OrganizationError):
    pass


class OrganizationAlreadyBootstrappedError(OrganizationError):
    pass


class OrganizationNotFoundError(OrganizationError):
    pass


class OrganizationInvalidBootstrapTokenError(OrganizationError):
    pass


class OrganizationFirstUserCreationError(OrganizationError):
    pass


class OrganizationExpiredError(OrganizationError):
    pass


@attr.s(slots=True, frozen=True, auto_attribs=True)
class Organization:
    organization_id: OrganizationID
    bootstrap_token: str
    expiration_date: Union[UnsetType, Optional[DateTime]] = Unset
    root_verify_key: Optional[VerifyKey] = None
    user_profile_outsider_allowed: bool = False
    active_users_limit: Optional[int] = None

    def is_bootstrapped(self):
        return self.root_verify_key is not None

    @property
    def is_expired(self):
        return isinstance(self.expiration_date, DateTime) and self.expiration_date <= DateTime.now()

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UsersPerProfileDetailItem:
    profile: UserProfile
    active: int
    revoked: int


@attr.s(slots=True, frozen=True, auto_attribs=True)
class OrganizationStats:
    data_size: int
    metadata_size: int
    users: int
    active_users: int
    workspaces: int
    users_per_profile_detail: List[UsersPerProfileDetailItem]


class BaseOrganizationComponent:
    def __init__(
        self, webhooks: WebhooksComponent, config: BackendConfig, bootstrap_token_size: int = 32
    ):
        self.webhooks = webhooks
        self.bootstrap_token_size = bootstrap_token_size
        self._config = config

    @api("organization_create", handshake_types=[APIV1_HandshakeType.ADMINISTRATION])
    @catch_protocol_errors
    async def api_organization_create(self, client_ctx, msg):
        msg = apiv1_organization_create_serializer.req_load(msg)

        bootstrap_token = token_hex(self.bootstrap_token_size)
        expiration_date = msg.get("expiration_date", Unset)
        default_users_limit = self._config.default_users_limit
        try:
            await self.create(
                msg["organization_id"],
                bootstrap_token=bootstrap_token,
                expiration_date=expiration_date,
                active_users_limit=default_users_limit,
            )

        except OrganizationAlreadyExistsError:
            return {"status": "already_exists"}

        rep = {
            "active_users_limit": default_users_limit,
            "bootstrap_token": bootstrap_token,
            "status": "ok",
        }
        if isinstance(expiration_date, DateTime):
            rep["expiration_date"] = expiration_date

        return apiv1_organization_create_serializer.rep_dump(rep)

    @api("organization_status", handshake_types=[APIV1_HandshakeType.ADMINISTRATION])
    @catch_protocol_errors
    async def api_organization_status(self, client_ctx, msg):
        msg = apiv1_organization_status_serializer.req_load(msg)

        try:
            organization = await self.get(msg["organization_id"])

        except OrganizationNotFoundError:
            return {"status": "not_found"}

        rep = {
            "is_bootstrapped": organization.is_bootstrapped(),
            "user_profile_outsider_allowed": organization.user_profile_outsider_allowed,
            "active_users_limit": organization.active_users_limit,
            "status": "ok",
        }

        if isinstance(organization.expiration_date, DateTime):
            rep["expiration_date"] = organization.expiration_date

        return apiv1_organization_status_serializer.rep_dump(rep)

    @api("organization_config", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_authenticated_organization_config(self, client_ctx, msg):
        msg = organization_config_serializer.req_load(msg)
        organization_id = client_ctx.organization_id
        try:
            organization = await self.get(organization_id)

        except OrganizationNotFoundError:
            return {"status": "not_found"}

        rep = {
            "user_profile_outsider_allowed": organization.user_profile_outsider_allowed,
            "active_users_limit": organization.active_users_limit,
            "status": "ok",
        }

        if isinstance(organization.expiration_date, DateTime):
            rep["expiration_date"] = organization.expiration_date

        return organization_config_serializer.rep_dump(rep)

    @api("organization_stats", handshake_types=[HandshakeType.AUTHENTICATED])
    @catch_protocol_errors
    async def api_authenticated_organization_stats(self, client_ctx, msg):
        msg = organization_stats_serializer.req_load(msg)

        if client_ctx.profile != UserProfile.ADMIN:
            return {
                "status": "not_allowed",
                "reason": f"User `{client_ctx.device_id.user_id}` is not admin",
            }
        # Get organization of the user
        organization_id = client_ctx.organization_id
        try:
            stats = await self.stats(organization_id)

        except OrganizationNotFoundError:
            return {"status": "not_found"}

        return organization_stats_serializer.rep_dump(
            {
                "status": "ok",
                "users": stats.users,
                "active_users": stats.active_users,
                "data_size": stats.data_size,
                "metadata_size": stats.metadata_size,
                "users_per_profile_detail": stats.users_per_profile_detail,
            }
        )

    @api("organization_stats", handshake_types=[APIV1_HandshakeType.ADMINISTRATION])
    @catch_protocol_errors
    async def apiv1_administration_organization_stats(self, client_ctx, msg):
        msg = apiv1_organization_stats_serializer.req_load(msg)

        try:
            stats = await self.stats(msg["organization_id"])

        except OrganizationNotFoundError:
            return {"status": "not_found"}

        return apiv1_organization_stats_serializer.rep_dump(
            {
                "status": "ok",
                "users": stats.users,
                "active_users": stats.active_users,
                "users_per_profile_detail": stats.users_per_profile_detail,
                "data_size": stats.data_size,
                "metadata_size": stats.metadata_size,
                "workspaces": stats.workspaces,
            }
        )

    @api("organization_update", handshake_types=[APIV1_HandshakeType.ADMINISTRATION])
    @catch_protocol_errors
    async def api_organization_update(self, client_ctx, msg):
        msg = apiv1_organization_update_serializer.req_load(msg)
        print(msg)
        try:
            await self.update(msg.pop("organization_id"), **msg)
        except OrganizationNotFoundError:
            return {"status": "not_found"}

        return apiv1_organization_update_serializer.rep_dump({"status": "ok"})

    @api("organization_bootstrap", handshake_types=[APIV1_HandshakeType.ANONYMOUS])
    @catch_protocol_errors
    async def api_organization_bootstrap(self, client_ctx, msg):
        msg = apiv1_organization_bootstrap_serializer.req_load(msg)
        bootstrap_token = msg["bootstrap_token"]
        root_verify_key = msg["root_verify_key"]

        try:
            u_data = UserCertificateContent.verify_and_load(
                msg["user_certificate"], author_verify_key=root_verify_key, expected_author=None
            )
            d_data = DeviceCertificateContent.verify_and_load(
                msg["device_certificate"], author_verify_key=root_verify_key, expected_author=None
            )

            ru_data = rd_data = None
            if "redacted_user_certificate" in msg:
                ru_data = UserCertificateContent.verify_and_load(
                    msg["redacted_user_certificate"],
                    author_verify_key=root_verify_key,
                    expected_author=None,
                )
            if "redacted_device_certificate" in msg:
                rd_data = DeviceCertificateContent.verify_and_load(
                    msg["redacted_device_certificate"],
                    author_verify_key=root_verify_key,
                    expected_author=None,
                )

        except DataError as exc:
            return {
                "status": "invalid_certification",
                "reason": f"Invalid certification data ({exc}).",
            }
        if u_data.profile != UserProfile.ADMIN:
            return {
                "status": "invalid_data",
                "reason": "Bootstrapping user must have admin profile.",
            }

        if u_data.timestamp != d_data.timestamp:
            return {
                "status": "invalid_data",
                "reason": "Device and user certificates must have the same timestamp.",
            }

        if u_data.user_id != d_data.device_id.user_id:
            return {
                "status": "invalid_data",
                "reason": "Device and user must have the same user ID.",
            }

        now = pendulum.now()
        if not timestamps_in_the_ballpark(u_data.timestamp, now):
            return {
                "status": "invalid_certification",
                "reason": f"Invalid timestamp in certification.",
            }

        if ru_data:
            if ru_data.evolve(human_handle=u_data.human_handle) != u_data:
                return {
                    "status": "invalid_data",
                    "reason": "Redacted User certificate differs from User certificate.",
                }
            if ru_data.human_handle:
                return {
                    "status": "invalid_data",
                    "reason": "Redacted User certificate must not contain a human_handle field.",
                }

        if rd_data:
            if rd_data.evolve(device_label=d_data.device_label) != d_data:
                return {
                    "status": "invalid_data",
                    "reason": "Redacted Device certificate differs from Device certificate.",
                }
            if rd_data.device_label:
                return {
                    "status": "invalid_data",
                    "reason": "Redacted Device certificate must not contain a device_label field.",
                }

        if (rd_data and not ru_data) or (ru_data and not rd_data):
            return {
                "status": "invalid_data",
                "reason": "Redacted user&device certificate muste be provided together",
            }

        user = User(
            user_id=u_data.user_id,
            human_handle=u_data.human_handle,
            profile=u_data.profile,
            user_certificate=msg["user_certificate"],
            redacted_user_certificate=msg.get("redacted_user_certificate", msg["user_certificate"]),
            user_certifier=u_data.author,
            created_on=u_data.timestamp,
        )
        first_device = Device(
            device_id=d_data.device_id,
            device_label=d_data.device_label,
            device_certificate=msg["device_certificate"],
            redacted_device_certificate=msg.get(
                "redacted_device_certificate", msg["device_certificate"]
            ),
            device_certifier=d_data.author,
            created_on=d_data.timestamp,
        )
        try:
            await self.bootstrap(
                client_ctx.organization_id, user, first_device, bootstrap_token, root_verify_key
            )

        except OrganizationAlreadyBootstrappedError:
            return {"status": "already_bootstrapped"}

        except (OrganizationNotFoundError, OrganizationInvalidBootstrapTokenError):
            return {"status": "not_found"}

        organization = await self.get(client_ctx.organization_id)

        # Note: we let OrganizationFirstUserCreationError bubbles up given
        # it should not occurs under normal circumstances

        # Finally notify webhook
        await self.webhooks.on_organization_bootstrap(
            organization_id=client_ctx.organization_id,
            device_id=first_device.device_id,
            device_label=first_device.device_label,
            human_email=user.human_handle.email if user.human_handle else None,
            human_label=user.human_handle.label if user.human_handle else None,
            active_users_limit=organization.active_users_limit,
        )

        return apiv1_organization_bootstrap_serializer.rep_dump({"status": "ok"})

    async def create(
        self,
        id: OrganizationID,
        bootstrap_token: str,
        expiration_date: Union[UnsetType, Optional[DateTime]] = Unset,
        active_users_limit: Optional[int] = None,
    ) -> None:
        """
        Raises:
            OrganizationAlreadyExistsError
        """
        raise NotImplementedError()

    async def get(self, id: OrganizationID) -> Organization:
        """
        Raises:
            OrganizationNotFoundError
        """
        raise NotImplementedError()

    async def bootstrap(
        self,
        id: OrganizationID,
        user: User,
        first_device: Device,
        bootstrap_token: str,
        root_verify_key: VerifyKey,
    ) -> None:
        """
        Raises:
            OrganizationNotFoundError
            OrganizationAlreadyBootstrappedError
            OrganizationInvalidBootstrapTokenError
            OrganizationFirstUserCreationError
        """
        raise NotImplementedError()

    async def stats(self, id: OrganizationID) -> OrganizationStats:
        """
        Raises:
            OrganizationNotFoundError
        """
        raise NotImplementedError()

    async def update(
        self,
        id: OrganizationID,
        expiration_date: Union[UnsetType, Optional[DateTime]] = Unset,
        user_profile_outsider_allowed: Union[UnsetType, bool] = Unset,
        active_users_limit: Union[UnsetType, int] = Unset,
    ):
        """
        Raises:
            OrganizationNotFoundError
        """
        raise NotImplementedError()
