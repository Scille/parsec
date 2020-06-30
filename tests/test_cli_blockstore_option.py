# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from click import BadParameter

from parsec.backend.cli.run import _parse_blockstore_params
from parsec.backend.config import (
    MockedBlockStoreConfig,
    PostgreSQLBlockStoreConfig,
    RAID0BlockStoreConfig,
    S3BlockStoreConfig,
    SWIFTBlockStoreConfig,
)


def test_parse_mocked():
    config = _parse_blockstore_params(["MOCKED"])
    assert config == MockedBlockStoreConfig()


def test_parse_postgresql():
    config = _parse_blockstore_params(["POSTGRESQL"])
    assert config == PostgreSQLBlockStoreConfig()


def test_parse_s3():
    config = _parse_blockstore_params(["s3:s3.example.com:region1:bucketA:key123:S3cr3t"])
    assert config == S3BlockStoreConfig(
        s3_endpoint_url="https://s3.example.com",
        s3_region="region1",
        s3_bucket="bucketA",
        s3_key="key123",
        s3_secret="S3cr3t",
    )


def test_parse_s3_with_default_endpoint():
    config = _parse_blockstore_params(["s3::region1:bucketA:key123:S3cr3t"])
    assert config == S3BlockStoreConfig(
        s3_endpoint_url=None,
        s3_region="region1",
        s3_bucket="bucketA",
        s3_key="key123",
        s3_secret="S3cr3t",
    )


def test_parse_s3_with_custom_url_scheme():
    config = _parse_blockstore_params(
        ["s3:http\\://s3.example.com:region1:bucketA:key123:\\:S3cr3t\\\\"]
    )
    assert config == S3BlockStoreConfig(
        s3_endpoint_url="http://s3.example.com",
        s3_region="region1",
        s3_bucket="bucketA",
        s3_key="key123",
        s3_secret=":S3cr3t\\",  # Also test escaping in password
    )


def test_parse_swift():
    config = _parse_blockstore_params(["swift:swift.example.com:tenant2:containerB:user123:S3cr3t"])
    assert config == SWIFTBlockStoreConfig(
        swift_authurl="https://swift.example.com",
        swift_tenant="tenant2",
        swift_container="containerB",
        swift_user="user123",
        swift_password="S3cr3t",
    )


def test_parse_swift_custom_url_scheme():
    config = _parse_blockstore_params(
        ["swift:http\\://swift.example.com:tenant2:containerB:user123:\\:S3cr3t\\\\"]
    )
    assert config == SWIFTBlockStoreConfig(
        swift_authurl="http://swift.example.com",
        swift_tenant="tenant2",
        swift_container="containerB",
        swift_user="user123",
        swift_password=":S3cr3t\\",  # Also test escaping in password
    )


def test_parse_simple_raid():
    config = _parse_blockstore_params(
        [
            "raid0:0:MOCKED",
            "raid0:1:POSTGRESQL",
            "raid0:2:s3:s3.example.com:region1:bucketA:key123:S3cr3t",
            "raid0:3:swift:swift.example.com:tenant2:containerB:user123:S3cr3t",
        ]
    )
    assert config == RAID0BlockStoreConfig(
        blockstores=[
            MockedBlockStoreConfig(),
            PostgreSQLBlockStoreConfig(),
            S3BlockStoreConfig(
                s3_endpoint_url="https://s3.example.com",
                s3_region="region1",
                s3_bucket="bucketA",
                s3_key="key123",
                s3_secret="S3cr3t",
            ),
            SWIFTBlockStoreConfig(
                swift_authurl="https://swift.example.com",
                swift_tenant="tenant2",
                swift_container="containerB",
                swift_user="user123",
                swift_password="S3cr3t",
            ),
        ]
    )


@pytest.mark.parametrize(
    "param",
    [
        "foo",  # Unknown type
        "s3:",  # Too few parts
        "s3:s3.example.com:region1:bucketA:key123:S3cr3t:dummy",  # Too much parts
    ],
)
def test_bad_single_param(param):
    with pytest.raises(BadParameter):
        _parse_blockstore_params([param])


@pytest.mark.parametrize(
    "param",
    [
        "foo",  # Unknown type
        "s3:",  # Too few parts
        "s3:s3.example.com:region1:bucketA:key123:S3cr3t:dummy",  # Too much parts
    ],
)
def test_invalid_mix_raid_params(param):
    with pytest.raises(BadParameter):
        _parse_blockstore_params([param])


@pytest.mark.parametrize(
    "params",
    [
        ["MOCKED", "MOCKED"],  # Multi params must be RAID
        ["raid0:0:MOCKED", "MOCKED"],  # Mixin RAID and non-RAID
        ["raid0:0:MOCKED", "raid1:1:MOCKED"],  # Mixin RAID types
        ["raid0:1:MOCKED", "raid0:2:MOCKED"],  # Hole in the nodes
        ["raid0:0:MOCKED", "raid0:2:MOCKED"],  # Hole in the nodes
        ["raid0:0:MOCKED", "raid0:0:MOCKED"],  # Same node multiple times
    ],
)
def test_bad_raid_params(params):
    with pytest.raises(BadParameter):
        _parse_blockstore_params(params)
