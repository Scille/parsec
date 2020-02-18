# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS
from functools import wraps

from typing import List, Tuple, Dict, Optional
from uuid import UUID
import pendulum

from parsec.utils import timestamps_in_the_ballpark
from parsec.api.protocol import (
    DeviceID,
    OrganizationID,
    vlob_create_serializer,
    vlob_read_serializer,
    vlob_update_serializer,
    vlob_poll_changes_serializer,
    vlob_list_versions_serializer,
    vlob_maintenance_get_reencryption_batch_serializer,
    vlob_maintenance_save_garbage_collection_batch_serializer,
    vlob_maintenance_get_garbage_collection_batch_serializer,
    vlob_maintenance_save_reencryption_batch_serializer,
)
from parsec.backend.utils import catch_protocol_errors


class VlobError(Exception):
    pass


class VlobAccessError(VlobError):
    pass


class VlobVersionError(VlobError):
    pass


class VlobTimestampError(VlobError):
    pass


class VlobNotFoundError(VlobError):
    pass


class VlobAlreadyExistsError(VlobError):
    pass


class VlobEncryptionRevisionError(VlobError):
    pass


class VlobInMaintenanceError(VlobError):
    pass


class VlobNotInMaintenanceError(VlobError):
    pass


class VlobMaintenanceError(VlobError):
    pass


def _catch_common_in_maintenance_errors(serializer):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)

            except VlobNotInMaintenanceError as exc:
                return serializer.rep_dump({"status": "not_in_maintenance", "reason": str(exc)})

            except VlobMaintenanceError as exc:
                return serializer.rep_dump({"status": "maintenance_error", "reason": str(exc)})

        return wrapper

    return decorator


def _catch_common_vlob_errors(serializer):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)

            except VlobAccessError:
                return serializer.rep_dump({"status": "not_allowed"})

            except VlobNotFoundError as exc:
                return serializer.rep_dump({"status": "not_found", "reason": str(exc)})

        return wrapper

    return decorator


def _catch_item_vlob_errors(serializer):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)

            except VlobVersionError:
                return vlob_update_serializer.rep_dump({"status": "bad_version"})

            except VlobTimestampError:
                return vlob_update_serializer.rep_dump({"status": "bad_timestamp"})

            except VlobEncryptionRevisionError:
                return vlob_create_serializer.rep_dump({"status": "bad_encryption_revision"})

            except VlobInMaintenanceError:
                return vlob_update_serializer.rep_dump({"status": "in_maintenance"})

        return wrapper

    return decorator


class BaseVlobComponent:
    @catch_protocol_errors
    async def api_vlob_create(self, client_ctx, msg):
        msg = vlob_create_serializer.req_load(msg)
        now = pendulum.now()
        if not timestamps_in_the_ballpark(msg["timestamp"], now):
            return {"status": "bad_timestamp", "reason": f"Timestamp is out of date."}

        try:
            await self.create(client_ctx.organization_id, client_ctx.device_id, **msg)

        except VlobAlreadyExistsError as exc:
            return vlob_create_serializer.rep_dump({"status": "already_exists", "reason": str(exc)})

        except VlobAccessError:
            return vlob_create_serializer.rep_dump({"status": "not_allowed"})

        except VlobEncryptionRevisionError:
            return vlob_create_serializer.rep_dump({"status": "bad_encryption_revision"})

        except VlobInMaintenanceError:
            return vlob_create_serializer.rep_dump({"status": "in_maintenance"})

        return vlob_create_serializer.rep_dump({"status": "ok"})

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_read_serializer)
    @_catch_item_vlob_errors(vlob_read_serializer)
    async def api_vlob_read(self, client_ctx, msg):
        msg = vlob_read_serializer.req_load(msg)
        version, blob, author, created_on = await self.read(
            client_ctx.organization_id, client_ctx.device_id, **msg
        )

        return vlob_read_serializer.rep_dump(
            {
                "status": "ok",
                "blob": blob,
                "version": version,
                "author": author,
                "timestamp": created_on,
            }
        )

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_update_serializer)
    @_catch_item_vlob_errors(vlob_update_serializer)
    async def api_vlob_update(self, client_ctx, msg):
        msg = vlob_update_serializer.req_load(msg)

        now = pendulum.now()
        if not timestamps_in_the_ballpark(msg["timestamp"], now):
            return {"status": "bad_timestamp", "reason": f"Timestamp is out of date."}

        await self.update(client_ctx.organization_id, client_ctx.device_id, **msg)

        return vlob_update_serializer.rep_dump({"status": "ok"})

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_poll_changes_serializer)
    async def api_vlob_poll_changes(self, client_ctx, msg):
        msg = vlob_poll_changes_serializer.req_load(msg)

        # TODO: raise error if too many events since offset ?
        try:
            checkpoint, changes = await self.poll_changes(
                client_ctx.organization_id,
                client_ctx.device_id,
                msg["realm_id"],
                msg["last_checkpoint"],
            )

        except VlobInMaintenanceError:
            return vlob_poll_changes_serializer.rep_dump({"status": "in_maintenance"})

        return vlob_poll_changes_serializer.rep_dump(
            {"status": "ok", "current_checkpoint": checkpoint, "changes": changes}
        )

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_list_versions_serializer)
    async def api_vlob_list_versions(self, client_ctx, msg):
        msg = vlob_list_versions_serializer.req_load(msg)

        try:
            versions_dict = await self.list_versions(
                client_ctx.organization_id, client_ctx.device_id, msg["vlob_id"]
            )

        except VlobInMaintenanceError:
            return vlob_list_versions_serializer.rep_dump({"status": "in_maintenance"})

        return vlob_list_versions_serializer.rep_dump({"status": "ok", "versions": versions_dict})

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_maintenance_get_garbage_collection_batch_serializer)
    @_catch_common_in_maintenance_errors(vlob_maintenance_get_garbage_collection_batch_serializer)
    async def api_vlob_maintenance_get_garbage_collection_batch(self, client_ctx, msg):
        msg = vlob_maintenance_get_garbage_collection_batch_serializer.req_load(msg)
        batch = await self.maintenance_get_garbage_collection_batch(
            client_ctx.organization_id, client_ctx.device_id, **msg
        )
        return vlob_maintenance_get_garbage_collection_batch_serializer.rep_dump(
            {
                "status": "ok",
                "batch": [
                    {"vlob_id": vlob_id, "version": version, "datetime": datetime, "blob": blob}
                    for vlob_id, version, (datetime, blob) in batch
                ],
            }
        )

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_maintenance_save_garbage_collection_batch_serializer)
    @_catch_common_in_maintenance_errors(vlob_maintenance_save_garbage_collection_batch_serializer)
    async def api_vlob_maintenance_save_garbage_collection_batch(self, client_ctx, msg):
        msg = vlob_maintenance_save_garbage_collection_batch_serializer.req_load(msg)
        total, done = await self.maintenance_save_garbage_collection_batch(
            client_ctx.organization_id,
            client_ctx.device_id,
            realm_id=msg["realm_id"],
            garbage_collection_revision=msg["garbage_collection_revision"],
            batch=[(x["vlob_id"], x["version"], x["blocks_to_erase"]) for x in msg["batch"]],
        )

        return vlob_maintenance_save_garbage_collection_batch_serializer.rep_dump(
            {"status": "ok", "total": total, "done": done}
        )

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_maintenance_get_reencryption_batch_serializer)
    @_catch_common_in_maintenance_errors(vlob_maintenance_get_reencryption_batch_serializer)
    async def api_vlob_maintenance_get_reencryption_batch(self, client_ctx, msg):
        msg = vlob_maintenance_get_reencryption_batch_serializer.req_load(msg)

        try:
            batch = await self.maintenance_get_reencryption_batch(
                client_ctx.organization_id, client_ctx.device_id, **msg
            )

        except VlobEncryptionRevisionError:
            return vlob_create_serializer.rep_dump({"status": "bad_encryption_revision"})

        return vlob_maintenance_get_reencryption_batch_serializer.rep_dump(
            {
                "status": "ok",
                "batch": [
                    {"vlob_id": vlob_id, "version": version, "blob": blob}
                    for vlob_id, version, blob in batch
                ],
            }
        )

    @catch_protocol_errors
    @_catch_common_vlob_errors(vlob_maintenance_save_reencryption_batch_serializer)
    @_catch_common_in_maintenance_errors(vlob_maintenance_save_reencryption_batch_serializer)
    async def api_vlob_maintenance_save_reencryption_batch(self, client_ctx, msg):
        msg = vlob_maintenance_save_reencryption_batch_serializer.req_load(msg)

        try:
            total, done = await self.maintenance_save_reencryption_batch(
                client_ctx.organization_id,
                client_ctx.device_id,
                realm_id=msg["realm_id"],
                encryption_revision=msg["encryption_revision"],
                batch=[(x["vlob_id"], x["version"], x["blob"]) for x in msg["batch"]],
            )

        except VlobEncryptionRevisionError:
            return vlob_create_serializer.rep_dump({"status": "bad_encryption_revision"})

        return vlob_maintenance_save_reencryption_batch_serializer.rep_dump(
            {"status": "ok", "total": total, "done": done}
        )

    async def create(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        vlob_id: UUID,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        """
        Raises:
            VlobAlreadyExistsError
            VlobEncryptionRevisionError: if encryption_revision mismatch
            VlobInMaintenanceError
        """
        raise NotImplementedError()

    async def read(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: Optional[int] = None,
        timestamp: Optional[pendulum.Pendulum] = None,
    ) -> Tuple[int, bytes, DeviceID, pendulum.Pendulum]:
        """
        Raises:
            VlobAccessError
            VlobVersionError
            VlobNotFoundError
            VlobEncryptionRevisionError: if encryption_revision mismatch
            VlobInMaintenanceError
        """
        raise NotImplementedError()

    async def update(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: int,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        """
        Raises:
            VlobAccessError
            VlobVersionError
            VlobTimestampError
            VlobNotFoundError
            VlobEncryptionRevisionError: if encryption_revision mismatch
            VlobInMaintenanceError
        """
        raise NotImplementedError()

    async def group_check(
        self, organization_id: OrganizationID, author: DeviceID, to_check: List[dict]
    ) -> List[dict]:
        """
        Raises: Nothing !
        """
        raise NotImplementedError()

    async def poll_changes(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID, checkpoint: int
    ) -> Tuple[int, Dict[UUID, int]]:
        """
        Raises:
            VlobInMaintenanceError
            VlobNotFoundError
            VlobAccessError
        """
        raise NotImplementedError()

    async def list_versions(
        self, organization_id: OrganizationID, author: DeviceID, vlob_id: UUID
    ) -> Dict[int, Tuple[pendulum.Pendulum, DeviceID]]:
        """
        Raises:
            VlobInMaintenanceError
            VlobNotFoundError
            VlobAccessError
        """
        raise NotImplementedError()

    async def maintenance_get_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        size: int,
    ) -> List[Tuple[UUID, int, bytes]]:
        """
        Raises:
            VlobNotFoundError
            VlobAccessError
            VlobEncryptionRevisionError
            VlobMaintenanceError: not in maintenance
        """
        raise NotImplementedError()

    async def maintenance_save_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        batch: List[Tuple[UUID, int, bytes]],
    ) -> Tuple[int, int]:
        """
        Raises:
            VlobNotFoundError
            VlobAccessError
            VlobEncryptionRevisionError
            VlobMaintenanceError: not in maintenance
        """
        raise NotImplementedError()
