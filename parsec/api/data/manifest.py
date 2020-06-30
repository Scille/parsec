# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from enum import Enum
from typing import FrozenDict, Optional, Tuple

import attr
from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data.base import (
    BaseAPISignedData,
    BaseData,
    BaseSchema,
    BaseSignedDataSchema,
    DataValidationError,
)
from parsec.api.data.entry import EntryID, EntryIDField, EntryName, EntryNameField
from parsec.api.protocol import RealmRole, RealmRoleField
from parsec.crypto import HashDigest, SecretKey
from parsec.serde import OneOfSchema, fields, post_load, validate
from parsec.types import UUID4


class BlockID(UUID4):
    pass


BlockIDField = fields.uuid_based_field_factory(BlockID)


class ManifestType(Enum):
    FILE_MANIFEST = "file_manifest"
    FOLDER_MANIFEST = "folder_manifest"
    WORKSPACE_MANIFEST = "workspace_manifest"
    USER_MANIFEST = "user_manifest"


class BlockAccess(BaseData):
    class SCHEMA_CLS(BaseSchema):
        id = BlockIDField(required=True)
        key = fields.SecretKey(required=True)
        offset = fields.Integer(required=True, validate=validate.Range(min=0))
        size = fields.Integer(required=True, validate=validate.Range(min=0))
        digest = fields.HashDigest(required=True)

        @post_load
        def make_obj(self, data):
            return BlockAccess(**data)

    id: BlockID
    key: SecretKey
    offset: int
    size: int
    digest: HashDigest


class WorkspaceEntry(BaseData):
    class SCHEMA_CLS(BaseSchema):
        name = EntryNameField(validate=validate.Length(min=1, max=256), required=True)
        id = EntryIDField(required=True)
        key = fields.SecretKey(required=True)
        encryption_revision = fields.Int(required=True, validate=validate.Range(min=0))
        encrypted_on = fields.DateTime(required=True)
        role_cached_on = fields.DateTime(required=True)
        role = RealmRoleField(required=True, allow_none=True)

        @post_load
        def make_obj(self, data):
            return WorkspaceEntry(**data)

    name: str
    id: EntryID
    key: SecretKey
    encryption_revision: int
    encrypted_on: Pendulum
    role_cached_on: Pendulum
    role: Optional[RealmRole]

    @classmethod
    def new(cls, name):
        now = pendulum_now()
        return WorkspaceEntry(
            name=name,
            id=EntryID(),
            key=SecretKey.generate(),
            encryption_revision=1,
            encrypted_on=now,
            role_cached_on=now,
            role=RealmRole.OWNER,
        )

    def is_revoked(self) -> bool:
        return self.role is None


class VerifyParentMixin:
    @classmethod
    def verify_and_load(
        cls, *args, expected_parent: Optional[EntryID] = None, **kwargs
    ) -> BaseAPISignedData:
        data = super().verify_and_load(*args, **kwargs)
        if expected_parent is not None and data.parent != expected_parent:
            raise DataValidationError(
                f"Invalid parent ID: expected `{expected_parent}`, got `{data.parent}`"
            )
        return data


class Manifest(BaseAPISignedData):
    class SCHEMA_CLS(OneOfSchema, BaseSignedDataSchema):
        type_field = "type"

        @property
        def type_schemas(self):
            return {
                ManifestType.FILE_MANIFEST: FileManifest.SCHEMA_CLS,
                ManifestType.FOLDER_MANIFEST: FolderManifest.SCHEMA_CLS,
                ManifestType.WORKSPACE_MANIFEST: WorkspaceManifest.SCHEMA_CLS,
                ManifestType.USER_MANIFEST: UserManifest.SCHEMA_CLS,
            }

        def get_obj_type(self, obj):
            return obj["type"]

    @classmethod
    def verify_and_load(
        cls,
        *args,
        expected_id: Optional[EntryID] = None,
        expected_version: Optional[int] = None,
        **kwargs,
    ) -> "Manifest":
        data = super().verify_and_load(*args, **kwargs)
        if data.author is None and data.version != 0:
            raise DataValidationError("Manifest cannot be signed by root verify key")
        if expected_id is not None and data.id != expected_id:
            raise DataValidationError(
                f"Invalid entry ID: expected `{expected_id}`, got `{data.id}`"
            )
        if expected_version is not None and data.version != expected_version:
            raise DataValidationError(
                f"Invalid version: expected `{expected_version}`, got `{data.version}`"
            )
        return data


class FolderManifest(VerifyParentMixin, Manifest):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(ManifestType.FOLDER_MANIFEST, required=True)
        id = EntryIDField(required=True)
        parent = EntryIDField(required=True)
        # Version 0 means the data is not synchronized (hence author sould be None)
        version = fields.Integer(required=True, validate=validate.Range(min=0))
        created = fields.DateTime(required=True)
        updated = fields.DateTime(required=True)
        children = fields.FrozenMap(
            EntryNameField(validate=validate.Length(min=1, max=256)),
            EntryIDField(required=True),
            required=True,
        )

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return FolderManifest(**data)

    id: EntryID
    parent: EntryID
    version: int
    created: Pendulum
    updated: Pendulum
    children: FrozenDict[EntryName, EntryID]


class FileManifest(VerifyParentMixin, Manifest):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(ManifestType.FILE_MANIFEST, required=True)
        id = EntryIDField(required=True)
        parent = EntryIDField(required=True)
        # Version 0 means the data is not synchronized (hence author sould be None)
        version = fields.Integer(required=True, validate=validate.Range(min=0))
        created = fields.DateTime(required=True)
        updated = fields.DateTime(required=True)
        size = fields.Integer(required=True, validate=validate.Range(min=0))
        blocksize = fields.Integer(required=True, validate=validate.Range(min=8))
        blocks = fields.FrozenList(fields.Nested(BlockAccess.SCHEMA_CLS), required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return FileManifest(**data)

    id: EntryID
    parent: EntryID
    version: int
    created: Pendulum
    updated: Pendulum
    size: int
    blocksize: int
    blocks: Tuple[BlockAccess]


class WorkspaceManifest(Manifest):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(ManifestType.WORKSPACE_MANIFEST, required=True)
        id = EntryIDField(required=True)
        # Version 0 means the data is not synchronized (hence author sould be None)
        version = fields.Integer(required=True, validate=validate.Range(min=0))
        created = fields.DateTime(required=True)
        updated = fields.DateTime(required=True)
        children = fields.FrozenMap(
            EntryNameField(validate=validate.Length(min=1, max=256)),
            EntryIDField(required=True),
            required=True,
        )

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return WorkspaceManifest(**data)

    id: EntryID
    version: int
    created: Pendulum
    updated: Pendulum
    children: FrozenDict[EntryName, EntryID]


class UserManifest(Manifest):
    class SCHEMA_CLS(BaseSignedDataSchema):
        type = fields.EnumCheckedConstant(ManifestType.USER_MANIFEST, required=True)
        id = EntryIDField(required=True)
        # Version 0 means the data is not synchronized (hence author sould be None)
        version = fields.Integer(required=True, validate=validate.Range(min=0))
        created = fields.DateTime(required=True)
        updated = fields.DateTime(required=True)
        last_processed_message = fields.Integer(required=True, validate=validate.Range(min=0))
        workspaces = fields.List(fields.Nested(WorkspaceEntry.SCHEMA_CLS), required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return UserManifest(**data)

    id: EntryID
    version: int
    created: Pendulum
    updated: Pendulum
    last_processed_message: int
    workspaces: Tuple[WorkspaceEntry] = attr.ib(converter=tuple)

    def get_workspace_entry(self, workspace_id: EntryID) -> WorkspaceEntry:
        return next((w for w in self.workspaces if w.id == workspace_id), None)
