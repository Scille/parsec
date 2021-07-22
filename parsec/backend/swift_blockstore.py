# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import trio
from unittest.mock import Mock
import pbr.version
from uuid import UUID
from functools import partial


original_version_info = pbr.version.VersionInfo


def side_effect(key):
    if key == "python-swiftclient":
        return "3.5.0"

    else:
        return original_version_info(key)


pbr.version.VersionInfo = Mock(side_effect=side_effect)

import swiftclient
from swiftclient.exceptions import ClientException

from parsec.api.protocol import OrganizationID
from parsec.backend.blockstore import BaseBlockStoreComponent
from parsec.backend.block import BlockAlreadyExistsError, BlockNotFoundError, BlockTimeoutError


class SwiftBlockStoreComponent(BaseBlockStoreComponent):
    def __init__(self, auth_url, tenant, container, user, password):
        self.swift_client = swiftclient.Connection(
            authurl=auth_url, user=":".join([user, tenant]), key=password
        )
        self._container = container
        self.swift_client.head_container(container)

    async def read(self, organization_id: OrganizationID, id: UUID) -> bytes:
        slug = f"{organization_id}/{id}"
        try:
            headers, obj = await trio.to_thread.run_sync(
                self.swift_client.get_object, self._container, slug
            )

        except ClientException as exc:
            if exc.http_status == 404:
                raise BlockNotFoundError() from exc

            else:
                raise BlockTimeoutError() from exc

        return obj

    async def create(self, organization_id: OrganizationID, id: UUID, block: bytes) -> None:
        slug = f"{organization_id}/{id}"
        try:
            _, obj = await trio.to_thread.run_sync(
                self.swift_client.get_object, self._container, slug
            )

        except ClientException as exc:
            if exc.http_status == 404:
                await trio.to_thread.run_sync(
                    partial(self.swift_client.put_object, self._container, slug, block)
                )
            else:
                raise BlockTimeoutError() from exc

        else:
            raise BlockAlreadyExistsError()
