# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Optional

import attr


class BaseBlockStoreConfig:
    pass


@attr.s(frozen=True, auto_attribs=True)
class RAID0BlockStoreConfig(BaseBlockStoreConfig):
    type = "RAID0"

    blockstores: List[BaseBlockStoreConfig]


@attr.s(frozen=True, auto_attribs=True)
class RAID1BlockStoreConfig(BaseBlockStoreConfig):
    type = "RAID1"

    blockstores: List[BaseBlockStoreConfig]


@attr.s(frozen=True, auto_attribs=True)
class RAID5BlockStoreConfig(BaseBlockStoreConfig):
    type = "RAID5"

    blockstores: List[BaseBlockStoreConfig]


@attr.s(frozen=True, auto_attribs=True)
class S3BlockStoreConfig(BaseBlockStoreConfig):
    type = "S3"

    s3_endpoint_url: Optional[str]
    s3_region: str
    s3_bucket: str
    s3_key: str
    s3_secret: str


@attr.s(frozen=True, auto_attribs=True)
class SWIFTBlockStoreConfig(BaseBlockStoreConfig):
    type = "SWIFT"

    swift_authurl: str
    swift_tenant: str
    swift_container: str
    swift_user: str
    swift_password: str


@attr.s(frozen=True, auto_attribs=True)
class PostgreSQLBlockStoreConfig(BaseBlockStoreConfig):
    type = "POSTGRESQL"


@attr.s(frozen=True, auto_attribs=True)
class MockedBlockStoreConfig(BaseBlockStoreConfig):
    type = "MOCKED"


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BackendConfig:
    administration_token: str

    db_url: str
    # If False, deleted data are only marked for deletion
    db_drop_deleted_data: bool
    db_min_connections: int
    db_max_connections: int

    blockstore_config: BaseBlockStoreConfig

    debug: bool

    @property
    def db_type(self):
        if self.db_url.upper() == "MOCKED":
            return "MOCKED"
        else:
            return "POSTGRESQL"
        return self._db_type
