# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

import attr

from parsec.api.protocol import DeviceID, OrganizationID, RealmRole
from parsec.backend.block import (
    BaseBlockComponent,
    BlockAccessError,
    BlockAlreadyExistsError,
    BlockInMaintenanceError,
    BlockNotFoundError,
)
from parsec.backend.blockstore import BaseBlockStoreComponent
from parsec.backend.realm import BaseRealmComponent, RealmNotFoundError


@attr.s(auto_attribs=True)
class BlockMeta:
    realm_id: UUID
    size: int


class MemoryBlockComponent(BaseBlockComponent):
    def __init__(self):
        self._blockmetas = {}
        self._blockstore_component = None
        self._realm_component = None

    def register_components(
        self, blockstore: BaseBlockStoreComponent, realm: BaseRealmComponent, **other_components
    ):
        self._blockstore_component = blockstore
        self._realm_component = realm

    def _check_realm_read_access(self, organization_id, realm_id, user_id):
        can_read_roles = (
            RealmRole.OWNER,
            RealmRole.MANAGER,
            RealmRole.CONTRIBUTOR,
            RealmRole.READER,
        )
        self._check_realm_access(organization_id, realm_id, user_id, can_read_roles)

    def _check_realm_write_access(self, organization_id, realm_id, user_id):
        can_write_roles = (RealmRole.OWNER, RealmRole.MANAGER, RealmRole.CONTRIBUTOR)
        self._check_realm_access(organization_id, realm_id, user_id, can_write_roles)

    def _check_realm_access(self, organization_id, realm_id, user_id, allowed_roles):
        try:
            realm = self._realm_component._get_realm(organization_id, realm_id)
        except RealmNotFoundError:
            raise BlockNotFoundError(f"Realm `{realm_id}` doesn't exist")

        if realm.roles.get(user_id) not in allowed_roles:
            raise BlockAccessError()

        if realm.status.in_maintenance:
            raise BlockInMaintenanceError(f"Realm `{realm_id}` is currently under maintenance")

    async def read(
        self, organization_id: OrganizationID, author: DeviceID, block_id: UUID
    ) -> bytes:
        try:
            blockmeta = self._blockmetas[(organization_id, block_id)]

        except KeyError:
            raise BlockNotFoundError()

        self._check_realm_read_access(organization_id, blockmeta.realm_id, author.user_id)

        return await self._blockstore_component.read(organization_id, block_id)

    async def create(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        block_id: UUID,
        realm_id: UUID,
        block: bytes,
    ) -> None:
        self._check_realm_write_access(organization_id, realm_id, author.user_id)

        await self._blockstore_component.create(organization_id, block_id, block)
        self._blockmetas[(organization_id, block_id)] = BlockMeta(realm_id, len(block))


class MemoryBlockStoreComponent(BaseBlockStoreComponent):
    def __init__(self):
        self._blocks = {}

    async def read(self, organization_id: OrganizationID, block_id: UUID) -> bytes:
        try:
            return self._blocks[(organization_id, block_id)]

        except KeyError:
            raise BlockNotFoundError()

    async def create(self, organization_id: OrganizationID, block_id: UUID, block: bytes) -> None:
        key = (organization_id, block_id)
        if key in self._blocks:
            # Should not happen if client play with uuid randomness
            raise BlockAlreadyExistsError()

        self._blocks[key] = block
