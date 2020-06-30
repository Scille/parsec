# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest import mock
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError as S3ClientError
from botocore.exceptions import EndpointConnectionError as S3EndpointConnectionError

from parsec.backend.block import BlockAlreadyExistsError, BlockNotFoundError, BlockTimeoutError
from parsec.backend.s3_blockstore import S3BlockStoreComponent


@pytest.mark.trio
async def test_s3_read():
    with mock.patch("boto3.client") as client_mock:
        client_mock.return_value = Mock()
        client_mock().head_bucket.return_value = True
        blockstore = S3BlockStoreComponent("europe", "parsec", "john", "secret")
        # Ok
        response_mock = Mock()
        response_mock.read.return_value = "content"
        client_mock().get_object.return_value = {"Body": response_mock}
        assert await blockstore.read("org42", 123) == "content"
        # Not found
        client_mock().get_object.side_effect = S3ClientError(
            error_response={"Error": {"Code": "404"}}, operation_name="GET"
        )
        with pytest.raises(BlockNotFoundError):
            assert await blockstore.read("org42", 123)
        # Connection error
        client_mock().get_object.side_effect = S3EndpointConnectionError(endpoint_url="url")
        with pytest.raises(BlockTimeoutError):
            assert await blockstore.read("org42", 123)
        # Unknown exception
        client_mock().get_object.side_effect = S3ClientError(
            error_response={"Error": {"Code": "401"}}, operation_name="GET"
        )
        with pytest.raises(BlockTimeoutError):
            assert await blockstore.read("org42", 123)


@pytest.mark.trio
async def test_s3_create():
    with mock.patch("boto3.client") as client_mock:
        client_mock.return_value = Mock()
        client_mock().head_container.return_value = True
        blockstore = S3BlockStoreComponent("europe", "parsec", "john", "secret")
        # Ok
        client_mock().head_object.side_effect = S3ClientError(
            error_response={"Error": {"Code": "404"}}, operation_name="HEAD"
        )
        await blockstore.create("org42", 123, "content")
        client_mock().put_object.assert_called_with(
            Bucket="parsec", Key="org42/123", Body="content"
        )
        client_mock().put_object.reset_mock()
        # Already exist
        client_mock().head_object.side_effect = None
        with pytest.raises(BlockAlreadyExistsError):
            await blockstore.create("org42", 123, "content")
        client_mock().put_object.assert_not_called()
        # Connection error at HEAD
        client_mock().head_object.side_effect = S3EndpointConnectionError(endpoint_url="url")
        with pytest.raises(BlockTimeoutError):
            await blockstore.create("org42", 123, "content")
        client_mock().put_object.assert_not_called()
        # Unknown exception at HEAD
        client_mock().head_object.side_effect = S3ClientError(
            error_response={"Error": {"Code": "401"}}, operation_name="HEAD"
        )
        with pytest.raises(BlockTimeoutError):
            await blockstore.create("org42", 123, "content")
        # Connection error at PUT
        client_mock().head_object.side_effect = S3ClientError(
            error_response={"Error": {"Code": "404"}}, operation_name="HEAD"
        )
        client_mock().put_object.side_effect = S3EndpointConnectionError(endpoint_url="url")
        with pytest.raises(BlockTimeoutError):
            await blockstore.create("org42", 123, "content")
        # Unknown exception at PUT
        client_mock().put_object.side_effect = S3ClientError(
            error_response={"Error": {"Code": "401"}}, operation_name="PUT"
        )
        with pytest.raises(BlockTimeoutError):
            await blockstore.create("org42", 123, "content")
