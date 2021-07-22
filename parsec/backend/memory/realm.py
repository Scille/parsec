# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import attr
import pendulum
from uuid import UUID
from typing import List, Dict, Optional, Tuple

from parsec.api.data import UserProfile
from parsec.api.protocol import DeviceID, UserID, OrganizationID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.realm import (
    MaintenanceType,
    RealmGrantedRole,
    BaseRealmComponent,
    RealmRole,
    RealmStatus,
    RealmStats,
    RealmAccessError,
    RealmIncompatibleProfileError,
    RealmAlreadyExistsError,
    RealmRoleAlreadyGranted,
    RealmNotFoundError,
    RealmEncryptionRevisionError,
    RealmParticipantsMismatchError,
    RealmMaintenanceError,
    RealmInMaintenanceError,
    RealmNotInMaintenanceError,
)
from parsec.backend.user import BaseUserComponent, UserNotFoundError
from parsec.backend.message import BaseMessageComponent
from parsec.backend.memory.vlob import MemoryVlobComponent
from parsec.backend.memory.block import MemoryBlockComponent


@attr.s
class Realm:
    status: RealmStatus = attr.ib(factory=lambda: RealmStatus(None, None, None, 1))
    checkpoint: int = attr.ib(default=0)
    granted_roles: List[RealmGrantedRole] = attr.ib(factory=list)

    @property
    def roles(self):
        roles = {}
        for x in sorted(self.granted_roles, key=lambda x: x.granted_on):
            if x.role is None:
                roles.pop(x.user_id, None)
            else:
                roles[x.user_id] = x.role
        return roles


class MemoryRealmComponent(BaseRealmComponent):
    def __init__(self, send_event):
        self._send_event = send_event
        self._user_component = None
        self._message_component = None
        self._vlob_component = None
        self._block_component = None
        self._realms = {}
        self._maintenance_reencryption_is_finished_hook = None

    def register_components(
        self,
        user: BaseUserComponent,
        message: BaseMessageComponent,
        vlob: MemoryVlobComponent,
        block: MemoryBlockComponent,
        **other_components,
    ):
        self._user_component = user
        self._message_component = message
        self._vlob_component = vlob
        self._block_component = block

    def _get_realm(self, organization_id, realm_id):
        try:
            return self._realms[(organization_id, realm_id)]
        except KeyError:
            raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    async def create(
        self, organization_id: OrganizationID, self_granted_role: RealmGrantedRole
    ) -> None:
        assert self_granted_role.granted_by is not None
        assert self_granted_role.granted_by.user_id == self_granted_role.user_id
        assert self_granted_role.role == RealmRole.OWNER

        key = (organization_id, self_granted_role.realm_id)
        if key not in self._realms:
            self._realms[key] = Realm(granted_roles=[self_granted_role])

            await self._send_event(
                BackendEvent.REALM_ROLES_UPDATED,
                organization_id=organization_id,
                author=self_granted_role.granted_by,
                realm_id=self_granted_role.realm_id,
                user=self_granted_role.user_id,
                role=self_granted_role.role,
            )

        else:
            raise RealmAlreadyExistsError()

    async def get_status(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
    ) -> RealmStatus:
        realm = self._get_realm(organization_id, realm_id)
        if author.user_id not in realm.roles:
            raise RealmAccessError()
        return realm.status

    async def get_stats(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
    ) -> RealmStats:
        realm = self._get_realm(organization_id, realm_id)
        if author.user_id not in realm.roles:
            raise RealmAccessError()

        blocks_size = 0
        vlobs_size = 0
        for value in self._block_component._blockmetas.values():
            if value.realm_id == realm_id:
                blocks_size += value.size
        for value in self._vlob_component._vlobs.values():
            if value.realm_id == realm_id:
                vlobs_size += sum(len(blob) for (blob, _, _) in value.data)

        return RealmStats(blocks_size=blocks_size, vlobs_size=vlobs_size)

    async def get_current_roles(
        self, organization_id: OrganizationID, realm_id: UUID
    ) -> Dict[UserID, RealmRole]:
        realm = self._get_realm(organization_id, realm_id)
        roles: Dict[UserID, RealmRole] = {}
        for x in realm.granted_roles:
            if x.role is None:
                roles.pop(x.user_id, None)
            else:
                roles[x.user_id] = x.role
        return roles

    async def get_role_certificates(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        since: pendulum.DateTime,
    ) -> List[bytes]:
        realm = self._get_realm(organization_id, realm_id)
        if author.user_id not in realm.roles:
            raise RealmAccessError()
        if since:
            return [x.certificate for x in realm.granted_roles if x.granted_on > since]
        else:
            return [x.certificate for x in realm.granted_roles]

    async def update_roles(
        self,
        organization_id: OrganizationID,
        new_role: RealmGrantedRole,
        recipient_message: Optional[bytes] = None,
    ) -> None:
        assert new_role.granted_by is not None
        assert new_role.granted_by.user_id != new_role.user_id

        # The only way for an OUTSIDER to be OWNER is to create his own realm
        # (given he needs to have one to store it user manifest).
        try:
            user = self._user_component._get_user(organization_id, new_role.user_id)
        except UserNotFoundError:
            raise RealmNotFoundError(f"User `{new_role.user_id}` doesn't exist")

        if user.profile == UserProfile.OUTSIDER and new_role.role in (
            RealmRole.MANAGER,
            RealmRole.OWNER,
        ):
            raise RealmIncompatibleProfileError(
                "User with OUTSIDER profile cannot be MANAGER or OWNER"
            )

        realm = self._get_realm(organization_id, new_role.realm_id)

        if realm.status.in_maintenance:
            raise RealmInMaintenanceError("Data realm is currently under maintenance")

        owner_only = (RealmRole.OWNER,)
        owner_or_manager = (RealmRole.OWNER, RealmRole.MANAGER)
        existing_user_role = realm.roles.get(new_role.user_id)
        needed_roles: Tuple[RealmRole, ...]
        if existing_user_role in owner_or_manager or new_role.role in owner_or_manager:
            needed_roles = owner_only
        else:
            needed_roles = owner_or_manager

        author_role = realm.roles.get(new_role.granted_by.user_id)
        if author_role not in needed_roles:
            raise RealmAccessError()

        if existing_user_role == new_role.role:
            raise RealmRoleAlreadyGranted()

        realm.granted_roles.append(new_role)

        await self._send_event(
            BackendEvent.REALM_ROLES_UPDATED,
            organization_id=organization_id,
            author=new_role.granted_by,
            realm_id=new_role.realm_id,
            user=new_role.user_id,
            role=new_role.role,
        )

        if recipient_message is not None:
            await self._message_component.send(
                organization_id,
                new_role.granted_by,
                new_role.user_id,
                new_role.granted_on,
                recipient_message,
            )

    async def start_reencryption_maintenance(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        per_participant_message: Dict[UserID, bytes],
        timestamp: pendulum.DateTime,
    ) -> None:
        realm = self._get_realm(organization_id, realm_id)
        if realm.roles.get(author.user_id) != RealmRole.OWNER:
            raise RealmAccessError()
        if realm.status.in_maintenance:
            raise RealmInMaintenanceError(f"Realm `{realm_id}` alrealy in maintenance")
        if encryption_revision != realm.status.encryption_revision + 1:
            raise RealmEncryptionRevisionError("Invalid encryption revision")
        now = pendulum.now()
        not_revoked_roles = set()
        for user_id in realm.roles.keys():
            user = await self._user_component.get_user(organization_id, user_id)
            if not user.revoked_on or user.revoked_on > now:
                not_revoked_roles.add(user_id)
        if per_participant_message.keys() ^ not_revoked_roles:
            raise RealmParticipantsMismatchError(
                "Realm participants and message recipients mismatch"
            )

        realm.status = RealmStatus(
            maintenance_type=MaintenanceType.REENCRYPTION,
            maintenance_started_on=timestamp,
            maintenance_started_by=author,
            encryption_revision=encryption_revision,
        )
        self._vlob_component._maintenance_reencryption_start_hook(
            organization_id, realm_id, encryption_revision
        )

        # Should first send maintenance event, then message to each participant

        await self._send_event(
            BackendEvent.REALM_MAINTENANCE_STARTED,
            organization_id=organization_id,
            author=author,
            realm_id=realm_id,
            encryption_revision=encryption_revision,
        )

        for recipient, msg in per_participant_message.items():
            await self._message_component.send(organization_id, author, recipient, timestamp, msg)

    async def finish_reencryption_maintenance(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
    ) -> None:
        realm = self._get_realm(organization_id, realm_id)
        if realm.roles.get(author.user_id) != RealmRole.OWNER:
            raise RealmAccessError()
        if not realm.status.in_maintenance:
            raise RealmNotInMaintenanceError(f"Realm `{realm_id}` not under maintenance")
        if encryption_revision != realm.status.encryption_revision:
            raise RealmEncryptionRevisionError("Invalid encryption revision")
        if not self._vlob_component._maintenance_reencryption_is_finished_hook(
            organization_id, realm_id, encryption_revision
        ):
            raise RealmMaintenanceError("Reencryption operations are not over")

        realm.status = RealmStatus(
            maintenance_type=None,
            maintenance_started_on=None,
            maintenance_started_by=None,
            encryption_revision=encryption_revision,
        )

        await self._send_event(
            BackendEvent.REALM_MAINTENANCE_FINISHED,
            organization_id=organization_id,
            author=author,
            realm_id=realm_id,
            encryption_revision=encryption_revision,
        )

    async def get_realms_for_user(
        self, organization_id: OrganizationID, user: UserID
    ) -> Dict[UUID, RealmRole]:
        user_realms = {}
        for (realm_org_id, realm_id), realm in self._realms.items():
            if realm_org_id != organization_id:
                continue
            try:
                user_realms[realm_id] = realm.roles[user]
            except KeyError:
                pass
        return user_realms
