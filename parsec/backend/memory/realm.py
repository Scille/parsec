# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import attr
import pendulum
from uuid import UUID
from typing import List, Set, Dict, Optional

from parsec.api.protocol import RealmRole
from parsec.api.protocol import DeviceID, UserID, OrganizationID
from parsec.backend.realm import (
    MaintenanceType,
    RealmGrantedRole,
    BaseRealmComponent,
    RealmStatus,
    RealmAccessError,
    RealmAlreadyExistsError,
    RealmRoleAlreadyGranted,
    RealmNotFoundError,
    RealmEncryptionRevisionError,
    RealmGarbageCollectionRevisionError,
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
    status: RealmStatus = attr.ib(factory=lambda: RealmStatus(None, None, None, 1, 0))
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
        self._realms = {}
        self._maintenance_reencryption_is_finished_hook = None
        self._maintenance_garbage_collection_is_finished_hook = None

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

    def _get_realm(self, organization_id: OrganizationID, realm_id: UUID):
        try:
            return self._realms[(organization_id, realm_id)]
        except KeyError:
            raise RealmNotFoundError(f"Realm `{realm_id}` doesn't exist")

    async def create(
        self, organization_id: OrganizationID, self_granted_role: RealmGrantedRole
    ) -> None:
        assert self_granted_role.granted_by.user_id == self_granted_role.user_id
        assert self_granted_role.role == RealmRole.OWNER

        key = (organization_id, self_granted_role.realm_id)
        if key not in self._realms:
            self._realms[key] = Realm(granted_roles=[self_granted_role])

            await self._send_event(
                "realm.roles_updated",
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

    async def get_current_roles(
        self, organization_id: OrganizationID, realm_id: UUID
    ) -> Dict[UserID, RealmRole]:
        realm = self._get_realm(organization_id, realm_id)
        roles = {}
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
        since: pendulum.Pendulum,
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
        assert new_role.granted_by.user_id != new_role.user_id

        try:
            self._user_component._get_user(organization_id, new_role.user_id)
        except UserNotFoundError:
            raise RealmNotFoundError(f"User `{new_role.user_id}` doesn't exist")

        realm = self._get_realm(organization_id, new_role.realm_id)

        if realm.status.in_maintenance:
            raise RealmInMaintenanceError("Data realm is currently under maintenance")

        owner_only = (RealmRole.OWNER,)
        owner_or_manager = (RealmRole.OWNER, RealmRole.MANAGER)
        existing_user_role = realm.roles.get(new_role.user_id)
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
            "realm.roles_updated",
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

    def _check_maintenance_starting_access(self, realm: Realm, realm_id: UUID, author: DeviceID):
        if realm.roles.get(author.user_id) != RealmRole.OWNER:
            raise RealmAccessError()
        if realm.status.in_maintenance:
            raise RealmInMaintenanceError(f"Realm `{realm_id}` alrealy in maintenance")

    async def _list_not_revoked_users(self, realm, organization_id):
        now = pendulum.now()
        not_revoked_users = set()
        for user_id in realm.roles.keys():
            user = await self._user_component.get_user(organization_id, user_id)
            if not user.revoked_on or user.revoked_on > now:
                not_revoked_users.add(user_id)
        else:
            return not_revoked_users

    async def _send_maintenance_starting_messages(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        garbage_collection_revision: int,
        timestamp: pendulum.Pendulum,
        per_participant_message: Dict[UserID, bytes],
    ):
        # Should first send maintenance event, then message to each participant
        await self._send_event(
            "realm.maintenance_started",
            organization_id=organization_id,
            author=author,
            realm_id=realm_id,
            encryption_revision=encryption_revision,
            garbage_collection_revision=garbage_collection_revision,
        )

        for recipient, msg in per_participant_message.items():
            await self._message_component.send(organization_id, author, recipient, timestamp, msg)

    def _check_per_participant_message_recipients(
        self, recipients_ids: Set[BaseUserComponent], not_revoked_roles: Set[BaseUserComponent]
    ):
        if recipients_ids ^ not_revoked_roles:
            raise RealmParticipantsMismatchError(
                "Realm participants and message recipients mismatch"
            )

    async def start_garbage_collection_maintenance(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        garbage_collection_revision: int,
        per_participant_message: Dict[UserID, bytes],
        timestamp: pendulum.Pendulum,
    ) -> None:
        realm = self._get_realm(organization_id, realm_id)
        self._check_maintenance_starting_access(realm, realm_id, author)
        if garbage_collection_revision != realm.status.garbage_collection_revision + 1:
            raise RealmGarbageCollectionRevisionError("Invalid encryption revision")
        not_revoked_users = await self._list_not_revoked_users(realm, organization_id)
        self._check_per_participant_message_recipients(
            set(per_participant_message.keys()), not_revoked_users
        )

        await self._vlob_component._maintenance_garbage_collection_start_hook(
            author, organization_id, realm_id, garbage_collection_revision
        )
        realm.status = RealmStatus(
            maintenance_type=MaintenanceType.GARBAGE_COLLECTION,
            maintenance_started_on=timestamp,
            maintenance_started_by=author,
            encryption_revision=realm.status.encryption_revision,
            garbage_collection_revision=garbage_collection_revision,
        )
        await self._send_maintenance_starting_messages(
            organization_id,
            author,
            realm_id,
            realm.status.encryption_revision,
            garbage_collection_revision,
            timestamp,
            per_participant_message,
        )

    async def finish_garbage_collection_maintenance(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        garbage_collection_revision: int,
    ) -> None:
        realm = self._get_realm(organization_id, realm_id)
        if realm.roles.get(author.user_id) != RealmRole.OWNER:
            raise RealmAccessError()
        if not realm.status.in_maintenance:
            raise RealmNotInMaintenanceError(f"Realm `{realm_id}` not under maintenance")
        if garbage_collection_revision != realm.status.garbage_collection_revision:
            raise RealmGarbageCollectionRevisionError("Invalid encryption revision")
        if not self._vlob_component._maintenance_garbage_collection_is_finished_hook(
            organization_id, realm_id
        ):
            raise RealmMaintenanceError("Garbage collection operations are not over")
        realm.status = RealmStatus(
            maintenance_type=None,
            maintenance_started_on=None,
            maintenance_started_by=None,
            encryption_revision=realm.status.encryption_revision,
            garbage_collection_revision=garbage_collection_revision,
        )
        await self._send_event(
            "realm.maintenance_finished",
            organization_id=organization_id,
            author=author,
            realm_id=realm_id,
            encryption_revision=realm.status.encryption_revision,
            garbage_collection_revision=garbage_collection_revision,
        )

    async def start_reencryption_maintenance(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        per_participant_message: Dict[UserID, bytes],
        timestamp: pendulum.Pendulum,
    ) -> None:
        realm = self._get_realm(organization_id, realm_id)
        self._check_maintenance_starting_access(realm, realm_id, author)
        if encryption_revision != realm.status.encryption_revision + 1:
            raise RealmEncryptionRevisionError("Invalid encryption revision")
        not_revoked_users = await self._list_not_revoked_users(realm, organization_id)
        self._check_per_participant_message_recipients(
            set(per_participant_message.keys()), not_revoked_users
        )

        realm.status = RealmStatus(
            maintenance_type=MaintenanceType.REENCRYPTION,
            maintenance_started_on=timestamp,
            maintenance_started_by=author,
            encryption_revision=encryption_revision,
            garbage_collection_revision=realm.status.garbage_collection_revision,
        )

        self._vlob_component._maintenance_reencryption_start_hook(
            organization_id, realm_id, encryption_revision
        )

        await self._send_maintenance_starting_messages(
            organization_id,
            author,
            realm_id,
            encryption_revision,
            realm.status.garbage_collection_revision,
            timestamp,
            per_participant_message,
        )

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
            garbage_collection_revision=realm.status.garbage_collection_revision,
        )

        await self._send_event(
            "realm.maintenance_finished",
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
