# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest import mock
from unittest.mock import Mock

import pytest
import swiftclient  # noqa
from swiftclient.exceptions import ClientException  # noqa

from parsec.backend.block import BlockAlreadyExistsError, BlockNotFoundError, BlockTimeoutError
from parsec.backend.swift_blockstore import SwiftBlockStoreComponent


@pytest.mark.trio
async def test_swift_get():
    with mock.patch("swiftclient.Connection") as connection_mock:
        connection_mock.return_value = Mock()
        connection_mock().head_container.return_value = True
        blockstore = SwiftBlockStoreComponent("http://url", "scille", "parsec", "john", "secret")
        # Ok
        connection_mock().get_object.return_value = True, "content"
        assert await blockstore.read("org42", 123) == "content"
        # Not found
        connection_mock().get_object.side_effect = ClientException(http_status=404, msg="")
        with pytest.raises(BlockNotFoundError):
            assert await blockstore.read("org42", 123)
        # Other exception
        connection_mock().get_object.side_effect = ClientException(http_status=500, msg="")
        with pytest.raises(BlockTimeoutError):
            assert await blockstore.read("org42", 123)


@pytest.mark.trio
async def test_swift_post():
    with mock.patch("swiftclient.Connection") as connection_mock:
        connection_mock.return_value = Mock()
        connection_mock().head_container.return_value = True
        blockstore = SwiftBlockStoreComponent("http://url", "scille", "parsec", "john", "secret")
        # Ok
        connection_mock().get_object.side_effect = ClientException(http_status=404, msg="")
        await blockstore.create("org42", 123, "content")
        connection_mock().put_object.assert_called_with("parsec", "org42/123", "content")
        # Already exists
        connection_mock().get_object.side_effect = None
        connection_mock().get_object.return_value = True, "content"
        with pytest.raises(BlockAlreadyExistsError):
            await blockstore.create("org42", 123, "content")
        # Other exception
        connection_mock().get_object.side_effect = ClientException(http_status=500, msg="")
        with pytest.raises(BlockTimeoutError):
            await blockstore.create("org42", 123, "content")
