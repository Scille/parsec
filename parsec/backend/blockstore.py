# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from uuid import UUID

from parsec.api.protocol import OrganizationID
from parsec.backend.config import (
    BaseBlockStoreConfig,
    RAID0BlockStoreConfig,
    RAID1BlockStoreConfig,
    RAID5BlockStoreConfig,
    S3BlockStoreConfig,
    SWIFTBlockStoreConfig,
    PostgreSQLBlockStoreConfig,
    MockedBlockStoreConfig,
)


class BaseBlockStoreComponent:
    async def read(self, organization_id: OrganizationID, id: UUID) -> bytes:
        """
        Raises:
            BlockNotFoundError
            BlockTimeoutError
        """
        raise NotImplementedError()

    async def create(self, organization_id: OrganizationID, id: UUID, block: bytes) -> None:
        """
        Raises:
            BlockAlreadyExistsError
            BlockTimeoutError
        """
        raise NotImplementedError()


def blockstore_factory(
    config: BaseBlockStoreConfig, postgresql_dbh=None
) -> BaseBlockStoreComponent:
    if isinstance(config, MockedBlockStoreConfig):
        from parsec.backend.memory import MemoryBlockStoreComponent

        return MemoryBlockStoreComponent()

    elif isinstance(config, PostgreSQLBlockStoreConfig):
        from parsec.backend.postgresql import PGBlockStoreComponent

        if not postgresql_dbh:
            raise ValueError("PostgreSQL block store is not available")
        return PGBlockStoreComponent(postgresql_dbh)

    elif isinstance(config, S3BlockStoreConfig):
        try:
            from parsec.backend.s3_blockstore import S3BlockStoreComponent

            return S3BlockStoreComponent(
                config.s3_region,
                config.s3_bucket,
                config.s3_key,
                config.s3_secret,
                config.s3_endpoint_url,
            )
        except ImportError as exc:
            raise ValueError("S3 block store is not available") from exc

    elif isinstance(config, SWIFTBlockStoreConfig):
        try:
            from parsec.backend.swift_blockstore import SwiftBlockStoreComponent

            return SwiftBlockStoreComponent(
                config.swift_authurl,
                config.swift_tenant,
                config.swift_container,
                config.swift_user,
                config.swift_password,
            )
        except ImportError as exc:
            raise ValueError("Swift block store is not available") from exc

    elif isinstance(config, RAID1BlockStoreConfig):
        from parsec.backend.raid1_blockstore import RAID1BlockStoreComponent

        blocks = [blockstore_factory(subconf, postgresql_dbh) for subconf in config.blockstores]

        return RAID1BlockStoreComponent(blocks)

    elif isinstance(config, RAID0BlockStoreConfig):
        from parsec.backend.raid0_blockstore import RAID0BlockStoreComponent

        blocks = [blockstore_factory(subconf, postgresql_dbh) for subconf in config.blockstores]

        return RAID0BlockStoreComponent(blocks)

    elif isinstance(config, RAID5BlockStoreConfig):
        from parsec.backend.raid5_blockstore import RAID5BlockStoreComponent

        if len(config.blockstores) < 3:
            raise ValueError(f"RAID5 block store needs at least 3 nodes")

        blocks = [blockstore_factory(subconf, postgresql_dbh) for subconf in config.blockstores]

        return RAID5BlockStoreComponent(blocks)

    else:
        raise ValueError(f"Unknown block store configuration `{config}`")
