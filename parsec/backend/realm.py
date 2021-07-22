# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from typing import Dict, List, Optional
from uuid import UUID
import pendulum
import attr

from parsec.utils import timestamps_in_the_ballpark
from parsec.api.data import DataError, RealmRoleCertificateContent, UserProfile
from parsec.api.protocol import (
    OrganizationID,
    UserID,
    DeviceID,
    RealmRole,
    MaintenanceType,
    realm_status_serializer,
    realm_stats_serializer,
    realm_create_serializer,
    realm_get_role_certificates_serializer,
    realm_update_roles_serializer,
    realm_start_reencryption_maintenance_serializer,
    realm_finish_reencryption_maintenance_serializer,
)
from parsec.backend.utils import catch_protocol_errors, api


class RealmError(Exception):
    pass


class RealmAccessError(RealmError):
    pass


class RealmIncompatibleProfileError(RealmError):
    pass


class RealmNotFoundError(RealmError):
    pass


class RealmAlreadyExistsError(RealmError):
    pass


class RealmRoleAlreadyGranted(RealmError):
    pass


class RealmEncryptionRevisionError(RealmError):
    pass


class RealmParticipantsMismatchError(RealmError):
    pass


class RealmInMaintenanceError(RealmError):
    pass


class RealmNotInMaintenanceError(RealmError):
    pass


class RealmMaintenanceError(RealmError):
    pass


@attr.s(slots=True, frozen=True, auto_attribs=True)
class RealmStatus:
    maintenance_type: Optional[MaintenanceType]
    maintenance_started_on: Optional[pendulum.DateTime]
    maintenance_started_by: Optional[DeviceID]
    encryption_revision: int

    @property
    def in_maintenance(self) -> bool:
        return self.maintenance_type is not None

    @property
    def in_reencryption(self) -> bool:
        return self.maintenance_type == MaintenanceType.REENCRYPTION

    @property
    def in_garbage_collection(self) -> bool:
        return self.maintenance_type == MaintenanceType.GARBAGE_COLLECTION


@attr.s(slots=True, frozen=True, auto_attribs=True)
class RealmStats:
    blocks_size: int
    vlobs_size: int


@attr.s(slots=True, frozen=True, auto_attribs=True)
class RealmGrantedRole:
    def __repr__(self):
        return f"{self.__class__.__name__}({self.user_id} {self.role})"

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)

    certificate: bytes
    realm_id: UUID
    user_id: UserID
    role: Optional[RealmRole]
    granted_by: Optional[DeviceID]
    granted_on: pendulum.DateTime = attr.ib(factory=pendulum.now)


class BaseRealmComponent:
    @api("realm_create")
    @catch_protocol_errors
    async def api_realm_create(self, client_ctx, msg):
        msg = realm_create_serializer.req_load(msg)

        try:
            data = RealmRoleCertificateContent.verify_and_load(
                msg["role_certificate"],
                author_verify_key=client_ctx.verify_key,
                expected_author=client_ctx.device_id,
            )

        except DataError as exc:
            return {
                "status": "invalid_certification",
                "reason": f"Invalid certification data ({exc}).",
            }

        now = pendulum.now()
        if not timestamps_in_the_ballpark(data.timestamp, now):
            return {
                "status": "invalid_certification",
                "reason": f"Invalid timestamp in certification.",
            }

        granted_role = RealmGrantedRole(
            certificate=msg["role_certificate"],
            realm_id=data.realm_id,
            user_id=data.user_id,
            role=data.role,
            granted_by=data.author,
            granted_on=data.timestamp,
        )
        if granted_role.granted_by.user_id != granted_role.user_id:
            return {
                "status": "invalid_data",
                "reason": f"Initial realm role certificate must be self-signed.",
            }
        if granted_role.role != RealmRole.OWNER:
            return {
                "status": "invalid_data",
                "reason": f"Initial realm role certificate must set OWNER role.",
            }

        try:
            await self.create(client_ctx.organization_id, granted_role)

        except RealmNotFoundError as exc:
            return realm_create_serializer.rep_dump({"status": "not_found", "reason": str(exc)})

        except RealmAlreadyExistsError:
            return realm_create_serializer.rep_dump({"status": "already_exists"})

        return realm_create_serializer.rep_dump({"status": "ok"})

    @api("realm_status")
    @catch_protocol_errors
    async def api_realm_status(self, client_ctx, msg):
        msg = realm_status_serializer.req_load(msg)

        try:
            status = await self.get_status(
                client_ctx.organization_id, client_ctx.device_id, msg["realm_id"]
            )

        except RealmAccessError:
            return realm_status_serializer.rep_dump({"status": "not_allowed"})

        except RealmNotFoundError as exc:
            return realm_status_serializer.rep_dump({"status": "not_found", "reason": str(exc)})

        return realm_status_serializer.rep_dump(
            {
                "status": "ok",
                "in_maintenance": status.in_maintenance,
                "maintenance_type": status.maintenance_type,
                "maintenance_started_on": status.maintenance_started_on,
                "maintenance_started_by": status.maintenance_started_by,
                "encryption_revision": status.encryption_revision,
            }
        )

    @api("realm_stats")
    @catch_protocol_errors
    async def api_realm_stats(self, client_ctx, msg):
        msg = realm_stats_serializer.req_load(msg)
        try:
            stats = await self.get_stats(
                client_ctx.organization_id, client_ctx.device_id, msg["realm_id"]
            )
        except RealmAccessError:
            return realm_status_serializer.rep_dump({"status": "not_allowed"})
        except RealmNotFoundError as exc:
            return realm_status_serializer.rep_dump({"status": "not_found", "reason": str(exc)})
        return realm_stats_serializer.rep_dump(
            {"status": "ok", "blocks_size": stats.blocks_size, "vlobs_size": stats.vlobs_size}
        )

    @api("realm_get_role_certificates")
    @catch_protocol_errors
    async def api_realm_get_role_certificates(self, client_ctx, msg):
        msg = realm_get_role_certificates_serializer.req_load(msg)
        try:
            certificates = await self.get_role_certificates(
                client_ctx.organization_id, client_ctx.device_id, **msg
            )

        except RealmAccessError:
            return realm_get_role_certificates_serializer.rep_dump({"status": "not_allowed"})

        except RealmNotFoundError as exc:
            return realm_get_role_certificates_serializer.rep_dump(
                {"status": "not_found", "reason": str(exc)}
            )

        return realm_get_role_certificates_serializer.rep_dump(
            {"status": "ok", "certificates": certificates}
        )

    @api("realm_update_roles")
    @catch_protocol_errors
    async def api_realm_update_roles(self, client_ctx, msg):
        # An OUTSIDER is allowed to create a realm (given he needs to have one
        # to store it user manifest). However he cannot be MANAGER or OWNER in
        # a shared realm as well.
        # Hence the only way for him to be OWNER is to create a realm, and in
        # this case he cannot share this realm with anyone.
        # On top of that, we don't have to fetch the user profile from the
        # database before checking it given it cannot be updated.
        if client_ctx.profile == UserProfile.OUTSIDER:
            return {"status": "not_allowed", "reason": "Outsider user cannot share realm"}

        msg = realm_update_roles_serializer.req_load(msg)

        try:
            data = RealmRoleCertificateContent.verify_and_load(
                msg["role_certificate"],
                author_verify_key=client_ctx.verify_key,
                expected_author=client_ctx.device_id,
            )

        except DataError as exc:
            return {
                "status": "invalid_certification",
                "reason": f"Invalid certification data ({exc}).",
            }

        now = pendulum.now()
        if not timestamps_in_the_ballpark(data.timestamp, now):
            return {
                "status": "invalid_certification",
                "reason": f"Invalid timestamp in certification.",
            }

        granted_role = RealmGrantedRole(
            certificate=msg["role_certificate"],
            realm_id=data.realm_id,
            user_id=data.user_id,
            role=data.role,
            granted_by=data.author,
            granted_on=data.timestamp,
        )
        if granted_role.granted_by.user_id == granted_role.user_id:
            return {
                "status": "invalid_data",
                "reason": f"Realm role certificate cannot be self-signed.",
            }

        try:
            await self.update_roles(
                client_ctx.organization_id, granted_role, msg["recipient_message"]
            )

        except RealmRoleAlreadyGranted:
            return realm_update_roles_serializer.rep_dump({"status": "already_granted"})

        except RealmAccessError:
            return realm_update_roles_serializer.rep_dump({"status": "not_allowed"})

        except RealmIncompatibleProfileError as exc:
            return realm_update_roles_serializer.rep_dump(
                {"status": "incompatible_profile", "reason": str(exc)}
            )

        except RealmNotFoundError as exc:
            return realm_update_roles_serializer.rep_dump(
                {"status": "not_found", "reason": str(exc)}
            )

        except RealmInMaintenanceError:
            return realm_update_roles_serializer.rep_dump({"status": "in_maintenance"})

        return realm_update_roles_serializer.rep_dump({"status": "ok"})

    @api("realm_start_reencryption_maintenance")
    @catch_protocol_errors
    async def api_realm_start_reencryption_maintenance(self, client_ctx, msg):
        msg = realm_start_reencryption_maintenance_serializer.req_load(msg)

        now = pendulum.now()
        if not timestamps_in_the_ballpark(msg["timestamp"], now):
            return {"status": "bad_timestamp", "reason": "Timestamp is out of date."}

        try:
            await self.start_reencryption_maintenance(
                client_ctx.organization_id, client_ctx.device_id, **msg
            )

        except RealmAccessError:
            return realm_start_reencryption_maintenance_serializer.rep_dump(
                {"status": "not_allowed"}
            )

        except RealmNotFoundError as exc:
            return realm_start_reencryption_maintenance_serializer.rep_dump(
                {"status": "not_found", "reason": str(exc)}
            )

        except RealmEncryptionRevisionError:
            return realm_start_reencryption_maintenance_serializer.rep_dump(
                {"status": "bad_encryption_revision"}
            )

        except RealmParticipantsMismatchError as exc:
            return realm_start_reencryption_maintenance_serializer.rep_dump(
                {"status": "participants_mismatch", "reason": str(exc)}
            )

        except RealmMaintenanceError as exc:
            return realm_start_reencryption_maintenance_serializer.rep_dump(
                {"status": "maintenance_error", "reason": str(exc)}
            )

        except RealmInMaintenanceError:
            return realm_start_reencryption_maintenance_serializer.rep_dump(
                {"status": "in_maintenance"}
            )

        return realm_start_reencryption_maintenance_serializer.rep_dump({"status": "ok"})

    @api("realm_finish_reencryption_maintenance")
    @catch_protocol_errors
    async def api_realm_finish_reencryption_maintenance(self, client_ctx, msg):
        msg = realm_finish_reencryption_maintenance_serializer.req_load(msg)

        try:
            await self.finish_reencryption_maintenance(
                client_ctx.organization_id, client_ctx.device_id, **msg
            )

        except RealmAccessError:
            return realm_finish_reencryption_maintenance_serializer.rep_dump(
                {"status": "not_allowed"}
            )

        except RealmNotFoundError as exc:
            return realm_finish_reencryption_maintenance_serializer.rep_dump(
                {"status": "not_found", "reason": str(exc)}
            )

        except RealmEncryptionRevisionError:
            return realm_finish_reencryption_maintenance_serializer.rep_dump(
                {"status": "bad_encryption_revision"}
            )

        except RealmNotInMaintenanceError as exc:
            return realm_finish_reencryption_maintenance_serializer.rep_dump(
                {"status": "not_in_maintenance", "reason": str(exc)}
            )

        except RealmMaintenanceError as exc:
            return realm_finish_reencryption_maintenance_serializer.rep_dump(
                {"status": "maintenance_error", "reason": str(exc)}
            )

        return realm_finish_reencryption_maintenance_serializer.rep_dump({"status": "ok"})

    async def create(
        self, organization_id: OrganizationID, self_granted_role: RealmGrantedRole
    ) -> None:
        """
        Raises:
            RealmNotFoundError
            RealmAccessError
            RealmAlreadyExistsError
        """
        raise NotImplementedError()

    async def get_status(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
    ) -> RealmStatus:
        """
        Raises:
            RealmNotFoundError
            RealmAccessError
        """
        raise NotImplementedError()

    async def get_stats(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
    ) -> RealmStats:
        """
        Raises:
            RealmNotFoundError
            RealmAccessError
        """
        raise NotImplementedError()

    async def get_current_roles(
        self, organization_id: OrganizationID, realm_id: UUID
    ) -> Dict[UserID, RealmRole]:
        """
        Raises:
            RealmNotFoundError
        """
        raise NotImplementedError()

    async def get_role_certificates(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        since: pendulum.DateTime,
    ) -> List[bytes]:
        """
        Raises:
            RealmNotFoundError
            RealmAccessError
        """
        raise NotImplementedError()

    async def update_roles(
        self,
        organization_id: OrganizationID,
        new_role: RealmGrantedRole,
        recipient_message: Optional[bytes] = None,
    ) -> None:
        """
        Raises:
            RealmInMaintenanceError
            RealmNotFoundError
            RealmAccessError
            RealmIncompatibleProfileError
        """
        raise NotImplementedError()

    async def start_reencryption_maintenance(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        per_participant_message: Dict[UserID, bytes],
        timestamp: pendulum.DateTime,
    ) -> None:
        """
        Raises:
            RealmInMaintenanceError
            RealmMaintenanceError: bad encryption_revision or per_participant_message
            RealmNotFoundError
            RealmAccessError
        """
        raise NotImplementedError()

    async def finish_reencryption_maintenance(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
    ) -> None:
        """
        Raises:
            RealmNotFoundError
            RealmAccessError
            RealmMaintenanceError: not in maintenance or bad encryption_revision
        """
        raise NotImplementedError()

    async def get_realms_for_user(
        self, organization_id: OrganizationID, user: UserID
    ) -> Dict[UUID, RealmRole]:
        """
        Raises: Nothing !
        """
        raise NotImplementedError()
