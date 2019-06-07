# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import attr
import pendulum
from uuid import UUID
from typing import List, Tuple, Dict, Optional
from collections import defaultdict

from parsec.types import DeviceID, OrganizationID
from parsec.event_bus import EventBus
from parsec.api.protocole import RealmRole
from parsec.backend.drivers.memory.realm import (
    MemoryRealmComponent,
    RealmNotFoundError,
    RealmAlreadyExistsError,
)
from parsec.backend.vlob import (
    BaseVlobComponent,
    VlobAccessError,
    VlobVersionError,
    VlobNotFoundError,
    VlobAlreadyExistsError,
    VlobEncryptionRevisionError,
    VlobInMaintenanceError,
    VlobMaintenanceError,
)


@attr.s
class Vlob:
    realm_id: UUID = attr.ib()
    data: List[Tuple[bytes, DeviceID, pendulum.Pendulum]] = attr.ib(factory=list)

    @property
    def current_version(self):
        return len(self.data)


class Reencryption:
    def __init__(self, realm_id, vlobs):
        self.realm_id = realm_id
        self._original_vlobs = vlobs
        self._todo = {}
        self._done = {}
        for vlob_id, vlob in vlobs.items():
            for index, (data, _, _) in enumerate(vlob.data):
                version = index + 1
                self._todo[(vlob_id, version)] = data

    def get_reencrypted_vlobs(self):
        assert self.is_finished()
        vlobs = {}
        for (vlob_id, version), data in sorted(self._done.items()):
            try:
                (_, author, timestamp) = self._original_vlobs[vlob_id].data[version - 1]

            except KeyError:
                raise VlobNotFoundError()

            if vlob_id not in vlobs:
                vlobs[vlob_id] = Vlob(self.realm_id, [(data, author, timestamp)])
            else:
                vlobs[vlob_id].data.append((data, author, timestamp))
            assert len(vlobs[vlob_id].data) == version

        return vlobs

    def is_finished(self):
        return not self._todo

    def get_batch(self, size):
        batch = []
        for (vlob_id, version), data in self._todo.items():
            if (vlob_id, version) in self._done:
                continue
            batch.append((vlob_id, version, data))
        return batch[:size]

    def save_batch(self, batch):
        for vlob_id, version, data in batch:
            key = (vlob_id, version)
            if key in self._done:
                continue
            try:
                del self._todo[key]
            except KeyError:
                raise VlobNotFoundError()
            self._done[key] = data


@attr.s
class Changes:
    checkpoint: int = attr.ib(default=0)
    changes: Dict[UUID, Tuple[DeviceID, int, int]] = attr.ib(factory=dict)
    reencryption: Reencryption = attr.ib(default=None)


class MemoryVlobComponent(BaseVlobComponent):
    def __init__(self, event_bus: EventBus, realm_component: MemoryRealmComponent):
        self.event_bus = event_bus
        self._realm_component = realm_component
        self._vlobs = {}
        self._per_realm_changes = defaultdict(Changes)

    def _maintenance_reencryption_start_hook(self, organization_id, realm_id, encryption_revision):
        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert not changes.reencryption
        realm_vlobs = {
            vlob_id: vlob
            for (orgid, vlob_id), vlob in self._vlobs.items()
            if orgid == organization_id and vlob.realm_id == realm_id
        }
        changes.reencryption = Reencryption(realm_id, realm_vlobs)

    def _maintenance_reencryption_is_finished_hook(
        self, organization_id, realm_id, encryption_revision
    ):
        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.reencryption
        if not changes.reencryption.is_finished():
            return False

        realm_vlobs = changes.reencryption.get_reencrypted_vlobs()
        for vlob_id, vlob in realm_vlobs.items():
            self._vlobs[(organization_id, vlob_id)] = vlob
        changes.reencryption = None
        return True

    def _get_vlob(self, organization_id, vlob_id):
        try:
            return self._vlobs[(organization_id, vlob_id)]

        except KeyError:
            raise VlobNotFoundError(f"Vlob `{vlob_id}` doesn't exist")

    def _create_realm_if_needed(self, organization_id, realm_id, author):
        try:
            self._realm_component._create_realm(organization_id, realm_id, author)
        except RealmAlreadyExistsError:
            pass

    def _check_realm_read_access(self, organization_id, realm_id, user_id, encryption_revision):
        can_read_roles = (
            RealmRole.OWNER,
            RealmRole.MANAGER,
            RealmRole.CONTRIBUTOR,
            RealmRole.READER,
        )
        self._check_realm_access(
            organization_id, realm_id, user_id, encryption_revision, can_read_roles
        )

    def _check_realm_write_access(self, organization_id, realm_id, user_id, encryption_revision):
        can_write_roles = (RealmRole.OWNER, RealmRole.MANAGER, RealmRole.CONTRIBUTOR)
        self._check_realm_access(
            organization_id, realm_id, user_id, encryption_revision, can_write_roles
        )

    def _check_realm_access(
        self, organization_id, realm_id, user_id, encryption_revision, allowed_roles
    ):
        try:
            realm = self._realm_component._get_realm(organization_id, realm_id)
        except RealmNotFoundError:
            raise VlobNotFoundError(f"Realm `{realm_id}` doesn't exist")

        if realm.roles.get(user_id) not in allowed_roles:
            raise VlobAccessError()

        if encryption_revision not in (None, realm.status.encryption_revision):
            raise VlobEncryptionRevisionError()

        if realm.status.in_maintenance:
            raise VlobInMaintenanceError(f"Realm `{realm_id}` is currently under maintenance")

    def _check_realm_in_maintenance_access(
        self, organization_id, realm_id, user_id, encryption_revision
    ):
        can_do_maintenance_roles = (RealmRole.OWNER,)
        try:
            self._check_realm_access(
                organization_id, realm_id, user_id, encryption_revision, can_do_maintenance_roles
            )
        except VlobInMaintenanceError:
            pass
        else:
            raise VlobMaintenanceError(f"Realm `{realm_id}` not under maintenance")

    def _update_changes(self, organization_id, author, realm_id, src_id, src_version=1):
        changes = self._per_realm_changes[(organization_id, realm_id)]
        changes.checkpoint += 1
        changes.changes[src_id] = (author, changes.checkpoint, src_version)
        self.event_bus.send(
            "realm.vlobs_updated",
            organization_id=organization_id,
            author=author,
            realm_id=realm_id,
            checkpoint=changes.checkpoint,
            src_id=src_id,
            src_version=src_version,
        )

    async def create(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        vlob_id: UUID,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        self._create_realm_if_needed(organization_id, realm_id, author)

        self._check_realm_write_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        key = (organization_id, vlob_id)
        if key in self._vlobs:
            raise VlobAlreadyExistsError()

        self._vlobs[key] = Vlob(realm_id, [(blob, author, timestamp)])

        self._update_changes(organization_id, author, realm_id, vlob_id)

    async def read(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: Optional[int] = None,
        timestamp: Optional[pendulum.Pendulum] = None,
    ) -> Tuple[int, bytes, DeviceID, pendulum.Pendulum]:
        vlob = self._get_vlob(organization_id, vlob_id)

        self._check_realm_read_access(
            organization_id, vlob.realm_id, author.user_id, encryption_revision
        )

        if version is None:
            if timestamp is None:
                version = vlob.current_version
            else:
                for i in range(vlob.current_version, 0, -1):
                    if vlob.data[i - 1][2] <= timestamp:
                        version = i
                        break
                else:
                    raise VlobVersionError()
        try:
            return (version, *vlob.data[version - 1])

        except IndexError:
            raise VlobVersionError()

    async def update(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: int,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        vlob = self._get_vlob(organization_id, vlob_id)

        self._check_realm_write_access(
            organization_id, vlob.realm_id, author.user_id, encryption_revision
        )

        if version - 1 == vlob.current_version:
            vlob.data.append((blob, author, timestamp))
        else:
            raise VlobVersionError()

        self._update_changes(organization_id, author, vlob.realm_id, vlob_id, version)

    async def group_check(
        self, organization_id: OrganizationID, author: DeviceID, to_check: List[dict]
    ) -> List[dict]:
        changed = []
        for item in to_check:
            vlob_id = item["vlob_id"]
            version = item["version"]
            if version == 0:
                changed.append({"vlob_id": vlob_id, "version": version})
            else:
                try:
                    vlob = self._get_vlob(organization_id, vlob_id)
                except VlobNotFoundError:
                    continue

                try:
                    self._check_realm_read_access(
                        organization_id, vlob.realm_id, author.user_id, None
                    )
                except (VlobNotFoundError, VlobAccessError, VlobInMaintenanceError):
                    continue

                if vlob.current_version != version:
                    changed.append({"vlob_id": vlob_id, "version": vlob.current_version})

        return changed

    async def poll_changes(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID, checkpoint: int
    ) -> Tuple[int, Dict[UUID, int]]:
        self._check_realm_read_access(organization_id, realm_id, author.user_id, None)

        changes = self._per_realm_changes[(organization_id, realm_id)]
        changes_since_checkpoint = {
            src_id: src_version
            for src_id, (_, change_checkpoint, src_version) in changes.changes.items()
            if change_checkpoint > checkpoint
        }
        return (changes.checkpoint, changes_since_checkpoint)

    async def maintenance_get_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        size: int,
    ) -> List[Tuple[UUID, int, bytes]]:
        self._check_realm_in_maintenance_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.reencryption

        return changes.reencryption.get_batch(size)

    async def maintenance_save_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        batch: List[Tuple[UUID, int, bytes]],
    ) -> None:
        self._check_realm_in_maintenance_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.reencryption

        changes.reencryption.save_batch(batch)
