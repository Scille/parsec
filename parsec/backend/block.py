# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

from parsec.api.protocol import (
    DeviceID,
    OrganizationID,
    block_create_serializer,
    block_read_serializer,
)
from parsec.backend.utils import api, catch_protocol_errors


class BlockError(Exception):
    pass


class BlockAlreadyExistsError(BlockError):
    pass


class BlockNotFoundError(BlockError):
    pass


class BlockTimeoutError(BlockError):
    pass


class BlockAccessError(BlockError):
    pass


class BlockInMaintenanceError(BlockError):
    pass


class BaseBlockComponent:
    @api("block_read")
    @catch_protocol_errors
    async def api_block_read(self, client_ctx, msg):
        msg = block_read_serializer.req_load(msg)

        try:
            block = await self.read(client_ctx.organization_id, client_ctx.device_id, **msg)

        except BlockNotFoundError:
            return block_read_serializer.rep_dump({"status": "not_found"})

        except BlockTimeoutError:
            return block_read_serializer.rep_dump({"status": "timeout"})

        except BlockAccessError:
            return block_read_serializer.rep_dump({"status": "not_allowed"})

        except BlockInMaintenanceError:
            return block_read_serializer.rep_dump({"status": "in_maintenance"})

        return block_read_serializer.rep_dump({"status": "ok", "block": block})

    @api("block_create")
    @catch_protocol_errors
    async def api_block_create(self, client_ctx, msg):
        msg = block_create_serializer.req_load(msg)

        try:
            await self.create(client_ctx.organization_id, client_ctx.device_id, **msg)

        except BlockAlreadyExistsError:
            return block_create_serializer.rep_dump({"status": "already_exists"})

        except BlockNotFoundError:
            return block_create_serializer.rep_dump({"status": "not_found"})

        except BlockTimeoutError:
            return block_create_serializer.rep_dump({"status": "timeout"})

        except BlockAccessError:
            return block_create_serializer.rep_dump({"status": "not_allowed"})

        except BlockInMaintenanceError:
            return block_create_serializer.rep_dump({"status": "in_maintenance"})

        return block_create_serializer.rep_dump({"status": "ok"})

    async def read(
        self, organization_id: OrganizationID, author: DeviceID, block_id: UUID
    ) -> bytes:
        """
        Raises:
            BlockNotFoundError
            BlockTimeoutError
            BlockAccessError
            BlockInMaintenanceError
        """
        raise NotImplementedError()

    async def create(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        block_id: UUID,
        realm: UUID,
        block: bytes,
    ) -> None:
        """
        Raises:
            BlockNotFoundError: if cannot found realm
            BlockAlreadyExistsError
            BlockTimeoutError
            BlockAccessError
            BlockInMaintenanceError
        """
        raise NotImplementedError()
