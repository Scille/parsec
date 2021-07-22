# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from uuid import UUID

from parsec.api.protocol import OrganizationID
from parsec.backend.blockstore import BaseBlockStoreComponent


class RAID0BlockStoreComponent(BaseBlockStoreComponent):
    def __init__(self, blockstores):
        self.blockstores = blockstores

    def _get_blockstore(self, id: UUID):
        return self.blockstores[id.int % len(self.blockstores)]

    async def read(self, organization_id: OrganizationID, id: UUID) -> bytes:
        blockstore = self._get_blockstore(id)
        return await blockstore.read(organization_id, id)

    async def create(self, organization_id: OrganizationID, id: UUID, block: bytes) -> None:
        blockstore = self._get_blockstore(id)
        await blockstore.create(organization_id, id, block)
