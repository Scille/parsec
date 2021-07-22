# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import attr
import pendulum
from uuid import UUID
from typing import List, Tuple, Dict, Optional
from collections import defaultdict

from parsec.backend.backend_events import BackendEvent
from parsec.api.protocol import DeviceID, OrganizationID
from parsec.api.protocol import RealmRole
from parsec.backend.utils import OperationKind
from parsec.backend.realm import BaseRealmComponent, RealmNotFoundError
from parsec.backend.vlob import (
    BaseVlobComponent,
    VlobAccessError,
    VlobVersionError,
    VlobTimestampError,
    VlobNotFoundError,
    VlobAlreadyExistsError,
    VlobEncryptionRevisionError,
    VlobInMaintenanceError,
    VlobNotInMaintenanceError,
)


@attr.s
class Vlob:
    realm_id: UUID = attr.ib()
    data: List[Tuple[bytes, DeviceID, pendulum.DateTime]] = attr.ib(factory=list)

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
        self._total = len(self._todo)

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

        return self._total, len(self._done)


@attr.s
class Changes:
    checkpoint: int = attr.ib(default=0)
    changes: Dict[UUID, Tuple[DeviceID, int, int]] = attr.ib(factory=dict)
    reencryption: Reencryption = attr.ib(default=None)


class MemoryVlobComponent(BaseVlobComponent):
    def __init__(self, send_event):
        self._send_event = send_event
        self._realm_component = None
        self._vlobs = {}
        self._per_realm_changes = defaultdict(Changes)

    def register_components(self, realm: BaseRealmComponent, **other_components):
        self._realm_component = realm

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

    def _check_realm_read_access(self, organization_id, realm_id, user_id, encryption_revision):
        self._check_realm_access(
            organization_id, realm_id, user_id, encryption_revision, OperationKind.DATA_READ
        )

    def _check_realm_write_access(self, organization_id, realm_id, user_id, encryption_revision):
        self._check_realm_access(
            organization_id, realm_id, user_id, encryption_revision, OperationKind.DATA_WRITE
        )

    def _check_realm_access(
        self, organization_id, realm_id, user_id, encryption_revision, operation_kind
    ):
        try:
            realm = self._realm_component._get_realm(organization_id, realm_id)
        except RealmNotFoundError:
            raise VlobNotFoundError(f"Realm `{realm_id}` doesn't exist")

        # Only an owner can perform maintenance operation
        if operation_kind == OperationKind.MAINTENANCE:
            allowed_roles = (RealmRole.OWNER,)
        # All roles can do read-only operation
        elif operation_kind == OperationKind.DATA_READ:
            allowed_roles = (
                RealmRole.OWNER,
                RealmRole.MANAGER,
                RealmRole.CONTRIBUTOR,
                RealmRole.READER,
            )
        # All roles except reader can do write operation
        elif operation_kind == OperationKind.DATA_WRITE:
            allowed_roles = (RealmRole.OWNER, RealmRole.MANAGER, RealmRole.CONTRIBUTOR)
        else:
            assert False, f"Operation kind {operation_kind} not supported"

        # Check the role
        if realm.roles.get(user_id) not in allowed_roles:
            raise VlobAccessError()

        # Special case of reading while in reencryption
        if operation_kind == OperationKind.DATA_READ and realm.status.in_reencryption:
            # Starting a reencryption maintenance bumps the encryption revision.
            # Hence if we are currently in reencryption maintenance, last encryption revision is not ready
            # to be used (it will be once the reencryption is over !).
            # So during this intermediary state, we allow read access to the previous encryption revision instead.

            # Note that `encryption_revision` might also be `None` in the case of `poll_changes` and `list_versions`
            # requests, which should also be allowed during a reencryption

            # The vlob is not available yet for the current revision
            if (
                encryption_revision is not None
                and encryption_revision == realm.status.encryption_revision
            ):
                raise VlobInMaintenanceError(f"Realm `{realm_id}` is currently under maintenance")

            # The vlob is only available at the previous revision
            if (
                encryption_revision is not None
                and encryption_revision != realm.status.encryption_revision - 1
            ):
                raise VlobEncryptionRevisionError()

        # In all other cases
        else:
            # Writing during maintenance is forbidden
            if operation_kind != OperationKind.MAINTENANCE and realm.status.in_maintenance:
                raise VlobInMaintenanceError(f"Realm `{realm_id}` is currently under maintenance")

            # A maintenance state was expected
            if operation_kind == OperationKind.MAINTENANCE and not realm.status.in_maintenance:
                raise VlobNotInMaintenanceError(f"Realm `{realm_id}` not under maintenance")

            # Otherwise, simply check that the revisions match
            if (
                encryption_revision is not None
                and encryption_revision != realm.status.encryption_revision
            ):
                raise VlobEncryptionRevisionError()

    def _check_realm_in_maintenance_access(
        self, organization_id, realm_id, user_id, encryption_revision
    ):
        self._check_realm_access(
            organization_id, realm_id, user_id, encryption_revision, OperationKind.MAINTENANCE
        )

    async def _update_changes(self, organization_id, author, realm_id, src_id, src_version=1):
        changes = self._per_realm_changes[(organization_id, realm_id)]
        changes.checkpoint += 1
        changes.changes[src_id] = (author, changes.checkpoint, src_version)
        await self._send_event(
            BackendEvent.REALM_VLOBS_UPDATED,
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
        timestamp: pendulum.DateTime,
        blob: bytes,
    ) -> None:
        self._check_realm_write_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        key = (organization_id, vlob_id)
        if key in self._vlobs:
            raise VlobAlreadyExistsError()

        self._vlobs[key] = Vlob(realm_id, [(blob, author, timestamp)])

        await self._update_changes(organization_id, author, realm_id, vlob_id)

    async def read(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: Optional[int] = None,
        timestamp: Optional[pendulum.DateTime] = None,
    ) -> Tuple[int, bytes, DeviceID, pendulum.DateTime]:
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
            vlob_data, vlob_device_id, vlob_timestamp = vlob.data[version - 1]
            return (version, vlob_data, vlob_device_id, vlob_timestamp)

        except IndexError:
            raise VlobVersionError()

    async def update(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: int,
        timestamp: pendulum.DateTime,
        blob: bytes,
    ) -> None:
        vlob = self._get_vlob(organization_id, vlob_id)

        self._check_realm_write_access(
            organization_id, vlob.realm_id, author.user_id, encryption_revision
        )

        if version - 1 != vlob.current_version:
            raise VlobVersionError()
        if timestamp < vlob.data[vlob.current_version - 1][2]:
            raise VlobTimestampError(timestamp, vlob.data[vlob.current_version - 1][2])
        vlob.data.append((blob, author, timestamp))

        await self._update_changes(organization_id, author, vlob.realm_id, vlob_id, version)

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

    async def list_versions(
        self, organization_id: OrganizationID, author: DeviceID, vlob_id: UUID
    ) -> Dict[int, Tuple[pendulum.DateTime, DeviceID]]:
        vlobs = self._get_vlob(organization_id, vlob_id)

        self._check_realm_read_access(organization_id, vlobs.realm_id, author.user_id, None)
        return {k: (v[2], v[1]) for (k, v) in enumerate(vlobs.data, 1)}

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
    ) -> Tuple[int, int]:
        self._check_realm_in_maintenance_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.reencryption

        total, done = changes.reencryption.save_batch(batch)

        return total, done
