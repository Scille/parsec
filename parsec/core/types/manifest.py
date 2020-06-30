# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import functools
from enum import Enum
from typing import Optional, Tuple

import attr
from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data import (
    BaseData,
    BaseSchema,
    BlockAccess,
    BlockID,
    EntryID,
    EntryIDField,
    EntryName,
    EntryNameField,
)
from parsec.api.data import FileManifest as RemoteFileManifest
from parsec.api.data import FolderManifest as RemoteFolderManifest
from parsec.api.data import Manifest as RemoteManifest
from parsec.api.data import UserManifest as RemoteUserManifest
from parsec.api.data import WorkspaceEntry
from parsec.api.data import WorkspaceManifest as RemoteWorkspaceManifest
from parsec.api.protocol import DeviceID, RealmRole
from parsec.core.types.base import BaseLocalData
from parsec.crypto import HashDigest, SecretKey
from parsec.serde import OneOfSchema, fields, post_load, validate
from parsec.types import UUID4, FrozenDict

__all__ = (
    "WorkspaceEntry",  # noqa: Republishing
    "BlockAccess",  # noqa: Republishing
    "BlockID",  # noqa: Republishing
    "WorkspaceRole",
)


DEFAULT_BLOCK_SIZE = 512 * 1024  # 512 KB


# Cheap rename
WorkspaceRole = RealmRole


class ChunkID(UUID4):
    pass


ChunkIDField = fields.uuid_based_field_factory(ChunkID)


@functools.total_ordering
class Chunk(BaseData):
    """Represents a chunk of a data in file manifest.

    The raw data is identified by its `id` attribute and is aligned using the
    `raw_offset` attribute with respect to the file addressing. The raw data
    size is stored as `raw_size`.

    The `start` and `stop` attributes then describes the span of the actual data
    still with respect to the file addressing.

    This means the following rule applies:
        raw_offset <= start < stop <= raw_start + raw_size

    Access is an optional block access that can be used to produce a remote manifest
    when the chunk corresponds to an actual block within the context of this manifest.
    """

    class SCHEMA_CLS(BaseSchema):
        id = ChunkIDField(required=True)
        start = fields.Integer(required=True, validate=validate.Range(min=0))
        stop = fields.Integer(required=True, validate=validate.Range(min=1))
        raw_offset = fields.Integer(required=True, validate=validate.Range(min=0))
        raw_size = fields.Integer(required=True, validate=validate.Range(min=1))
        access = fields.Nested(BlockAccess.SCHEMA_CLS, required=True, allow_none=True)

        @post_load
        def make_obj(self, data):
            return Chunk(**data)

    id: ChunkID
    start: int
    stop: int
    raw_offset: int
    raw_size: int
    access: Optional[BlockAccess]

    # Ordering

    def __lt__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.start.__lt__(other)
        raise TypeError

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.start.__eq__(other)
        if isinstance(other, Chunk):
            return attr.astuple(self).__eq__(attr.astuple(other))
        raise TypeError

    # Properties

    @property
    def is_block(self):
        # Requires an access
        if self.access is None:
            return False
        # Pseudo block
        if not self.is_pseudo_block:
            return False
        # Offset inconsistent
        if self.raw_offset != self.access.offset:
            return False
        # Size inconsistent
        if self.raw_size != self.access.size:
            return False
        return True

    @property
    def is_pseudo_block(self):
        # Not left aligned
        if self.start != self.raw_offset:
            return False
        # Not right aligned
        if self.stop != self.raw_offset + self.raw_size:
            return False
        return True

    # Create

    @classmethod
    def new(cls, start: int, stop: int) -> "Chunk":
        assert start < stop
        return cls(
            id=ChunkID(),
            start=start,
            stop=stop,
            raw_offset=start,
            raw_size=stop - start,
            access=None,
        )

    @classmethod
    def from_block_acess(cls, block_access: BlockAccess):
        return cls(
            id=ChunkID(block_access.id),
            raw_offset=block_access.offset,
            raw_size=block_access.size,
            start=block_access.offset,
            stop=block_access.offset + block_access.size,
            access=block_access,
        )

    # Evolve

    def evolve_as_block(self, data: bytes) -> "Chunk":
        # No-op
        if self.is_block:
            return self

        # Check alignement
        if self.raw_offset != self.start:
            raise TypeError("This chunk is not aligned")

        # Craft access
        access = BlockAccess(
            id=BlockID(self.id),
            key=SecretKey.generate(),
            offset=self.start,
            size=self.stop - self.start,
            digest=HashDigest.from_data(data),
        )

        # Evolve
        return self.evolve(access=access)

    # Export

    def get_block_access(self) -> BlockAccess:
        if not self.is_block:
            raise TypeError("This chunk does not correspond to a block")
        return self.access


# Manifests data classes


class LocalManifestType(Enum):
    LOCAL_FILE_MANIFEST = "local_file_manifest"
    LOCAL_FOLDER_MANIFEST = "local_folder_manifest"
    LOCAL_WORKSPACE_MANIFEST = "local_workspace_manifest"
    LOCAL_USER_MANIFEST = "local_user_manifest"


class LocalManifest(BaseLocalData):
    class SCHEMA_CLS(OneOfSchema, BaseSchema):
        type_field = "type"

        @property
        def type_schemas(self):
            return {
                LocalManifestType.LOCAL_FILE_MANIFEST: LocalFileManifest.SCHEMA_CLS,
                LocalManifestType.LOCAL_FOLDER_MANIFEST: LocalFolderManifest.SCHEMA_CLS,
                LocalManifestType.LOCAL_WORKSPACE_MANIFEST: LocalWorkspaceManifest.SCHEMA_CLS,
                LocalManifestType.LOCAL_USER_MANIFEST: LocalUserManifest.SCHEMA_CLS,
            }

        def get_obj_type(self, obj):
            return obj["type"]

    # Properties

    @property
    def id(self):
        return self.base.id

    @property
    def created(self):
        return self.base.created

    @property
    def base_version(self):
        return self.base.version

    @property
    def is_placeholder(self):
        return self.base.version == 0

    # Evolve methods

    def evolve_and_mark_updated(self, **data) -> "LocalManifest":
        if "updated" not in data:
            data["updated"] = pendulum_now()
        data.setdefault("need_sync", True)
        return self.evolve(**data)

    # Remote methods

    @classmethod
    def from_remote(cls, remote: RemoteManifest) -> "LocalManifest":
        if isinstance(remote, RemoteFileManifest):
            return LocalFileManifest.from_remote(remote)
        elif isinstance(remote, RemoteFolderManifest):
            return LocalFolderManifest.from_remote(remote)
        elif isinstance(remote, RemoteWorkspaceManifest):
            return LocalWorkspaceManifest.from_remote(remote)
        elif isinstance(remote, RemoteUserManifest):
            return LocalUserManifest.from_remote(remote)

    def to_remote(self) -> RemoteManifest:
        raise NotImplementedError

    def match_remote(self, remote_manifest: RemoteFileManifest) -> bool:
        reference = self.to_remote(
            author=remote_manifest.author, timestamp=remote_manifest.timestamp
        )
        return reference.evolve(version=remote_manifest.version) == remote_manifest

    # Debugging

    def asdict(self):
        dct = attr.asdict(self)
        dct.pop("base")
        for name in "base_version", "is_placeholder", "created":
            dct[name] = getattr(self, name)
        if hasattr(self.base, "parent"):
            dct["parent"] = self.parent
        return dct


class LocalFileManifest(LocalManifest):
    class SCHEMA_CLS(BaseSchema):
        type = fields.EnumCheckedConstant(LocalManifestType.LOCAL_FILE_MANIFEST, required=True)
        base = fields.Nested(RemoteFileManifest.SCHEMA_CLS, required=True)
        need_sync = fields.Boolean(required=True)
        updated = fields.DateTime(required=True)
        size = fields.Integer(required=True, validate=validate.Range(min=0))
        blocksize = fields.Integer(required=True, validate=validate.Range(min=8))
        blocks = fields.FrozenList(
            fields.FrozenList(fields.Nested(Chunk.SCHEMA_CLS)), required=True
        )

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return LocalFileManifest(**data)

    base: RemoteFileManifest
    need_sync: bool
    updated: Pendulum
    size: int
    blocksize: int
    blocks: Tuple[Tuple[Chunk], ...]

    @classmethod
    def new_placeholder(
        cls, parent: EntryID, id: EntryID = None, now: Pendulum = None, blocksize=DEFAULT_BLOCK_SIZE
    ):
        now = now or pendulum_now()
        blocks = ()
        return cls(
            base=RemoteFileManifest(
                author=None,
                timestamp=now,
                id=id or EntryID(),
                parent=parent,
                version=0,
                created=now,
                updated=now,
                blocksize=blocksize,
                size=0,
                blocks=blocks,
            ),
            need_sync=True,
            updated=now,
            blocksize=blocksize,
            size=0,
            blocks=blocks,
        )

    # Properties

    @property
    def parent(self):
        return self.base.parent

    # Helper methods

    def get_chunks(self, block: int) -> Tuple[Chunk]:
        try:
            return self.blocks[block]
        except IndexError:
            return ()

    def is_reshaped(self) -> bool:
        for chunks in self.blocks:
            if len(chunks) != 1:
                return False
            if not chunks[0].is_block:
                return False
        return True

    def assert_integrity(self) -> None:
        current = 0
        assert isinstance(self.blocks, tuple)
        for i, chunks in enumerate(self.blocks):
            assert i * self.blocksize == current
            assert isinstance(chunks, tuple)
            assert len(chunks) > 0
            for chunk in chunks:
                assert chunk.start == current
                assert chunk.start < chunk.stop
                assert chunk.raw_offset <= chunk.start
                assert chunk.stop <= chunk.raw_offset + chunk.raw_size
                current = chunk.stop
        assert current == self.size

    # Remote methods

    @classmethod
    def from_remote(cls, remote: RemoteFileManifest) -> "LocalFileManifest":
        return cls(
            base=remote,
            need_sync=False,
            updated=remote.updated,
            size=remote.size,
            blocksize=remote.blocksize,
            blocks=tuple((Chunk.from_block_acess(block_access),) for block_access in remote.blocks),
        )

    def to_remote(self, author: DeviceID, timestamp: Pendulum = None) -> RemoteFileManifest:
        # Checks
        self.assert_integrity()
        assert self.is_reshaped()

        # Blocks
        blocks = tuple(chunks[0].get_block_access() for chunks in self.blocks)

        return RemoteFileManifest(
            author=author,
            timestamp=timestamp or pendulum_now(),
            id=self.id,
            parent=self.parent,
            version=self.base_version + 1,
            created=self.created,
            updated=self.updated,
            size=self.size,
            blocksize=self.blocksize,
            blocks=blocks,
        )

    def match_remote(self, remote_manifest: RemoteFileManifest) -> bool:
        if not self.is_reshaped():
            return False
        return super().match_remote(remote_manifest)


class LocalFolderManifest(LocalManifest):
    class SCHEMA_CLS(BaseSchema):
        type = fields.EnumCheckedConstant(LocalManifestType.LOCAL_FOLDER_MANIFEST, required=True)
        base = fields.Nested(RemoteFolderManifest.SCHEMA_CLS, required=True)
        need_sync = fields.Boolean(required=True)
        updated = fields.DateTime(required=True)
        children = fields.FrozenMap(EntryNameField(), EntryIDField(required=True), required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return LocalFolderManifest(**data)

    base: RemoteFolderManifest
    need_sync: bool
    updated: Pendulum
    children: FrozenDict[EntryName, EntryID]

    @classmethod
    def new_placeholder(cls, parent: EntryID, id: EntryID = None, now: Pendulum = None):
        now = now or pendulum_now()
        children = FrozenDict()
        return cls(
            base=RemoteFolderManifest(
                author=None,
                timestamp=now,
                id=id or EntryID(),
                parent=parent,
                version=0,
                created=now,
                updated=now,
                children=children,
            ),
            need_sync=True,
            updated=now,
            children=children,
        )

    # Properties

    @property
    def parent(self):
        return self.base.parent

    # Evolve methods

    def evolve_children_and_mark_updated(self, data) -> "LocalFolderManifest":
        return self.evolve_and_mark_updated(
            children={k: v for k, v in {**self.children, **data}.items() if v is not None}
        )

    def evolve_children(self, data) -> "LocalFolderManifest":
        return self.evolve(
            children={k: v for k, v in {**self.children, **data}.items() if v is not None}
        )

    # Remote methods

    @classmethod
    def from_remote(cls, remote: RemoteFolderManifest) -> "LocalFolderManifest":
        return cls(base=remote, need_sync=False, updated=remote.updated, children=remote.children)

    def to_remote(self, author: DeviceID, timestamp: Pendulum = None) -> RemoteFolderManifest:
        return RemoteFolderManifest(
            author=author,
            timestamp=timestamp or pendulum_now(),
            id=self.id,
            parent=self.parent,
            version=self.base_version + 1,
            created=self.created,
            updated=self.updated,
            children=self.children,
        )


class LocalWorkspaceManifest(LocalManifest):
    class SCHEMA_CLS(BaseSchema):
        type = fields.EnumCheckedConstant(LocalManifestType.LOCAL_WORKSPACE_MANIFEST, required=True)
        base = fields.Nested(RemoteWorkspaceManifest.SCHEMA_CLS, required=True)
        need_sync = fields.Boolean(required=True)
        updated = fields.DateTime(required=True)
        children = fields.FrozenMap(EntryNameField(), EntryIDField(required=True), required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return LocalWorkspaceManifest(**data)

    base: RemoteWorkspaceManifest
    need_sync: bool
    updated: Pendulum
    children: FrozenDict[EntryName, EntryID]

    @classmethod
    def new_placeholder(cls, id: EntryID = None, now: Pendulum = None):
        now = now or pendulum_now()
        children = FrozenDict()
        return cls(
            base=RemoteWorkspaceManifest(
                author=None,
                timestamp=now,
                id=id or EntryID(),
                version=0,
                created=now,
                updated=now,
                children=children,
            ),
            need_sync=True,
            updated=now,
            children=children,
        )

    # Evolve methods

    def evolve_children_and_mark_updated(self, data) -> "LocalWorkspaceManifest":
        return self.evolve_and_mark_updated(
            children={k: v for k, v in {**self.children, **data}.items() if v is not None}
        )

    def evolve_children(self, data) -> "LocalWorkspaceManifest":
        return self.evolve(
            children={k: v for k, v in {**self.children, **data}.items() if v is not None}
        )

    # Remote methods

    @classmethod
    def from_remote(cls, remote: RemoteWorkspaceManifest) -> "LocalWorkspaceManifest":
        return cls(base=remote, need_sync=False, updated=remote.updated, children=remote.children)

    def to_remote(self, author: DeviceID, timestamp: Pendulum = None) -> RemoteWorkspaceManifest:
        return RemoteWorkspaceManifest(
            author=author,
            timestamp=timestamp or pendulum_now(),
            id=self.id,
            version=self.base_version + 1,
            created=self.created,
            updated=self.updated,
            children=self.children,
        )


class LocalUserManifest(LocalManifest):
    class SCHEMA_CLS(BaseSchema):
        type = fields.EnumCheckedConstant(LocalManifestType.LOCAL_USER_MANIFEST, required=True)
        base = fields.Nested(RemoteUserManifest.SCHEMA_CLS, required=True)
        need_sync = fields.Boolean(required=True)
        updated = fields.DateTime(required=True)
        last_processed_message = fields.Integer(required=True, validate=validate.Range(min=0))
        workspaces = fields.FrozenList(fields.Nested(WorkspaceEntry.SCHEMA_CLS), required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return LocalUserManifest(**data)

    base: RemoteUserManifest
    need_sync: bool
    updated: Pendulum
    last_processed_message: int
    workspaces: Tuple[WorkspaceEntry, ...]

    @classmethod
    def new_placeholder(cls, id: EntryID = None, now: Pendulum = None) -> "LocalUserManifest":
        workspaces = ()
        now = now or pendulum_now()
        return cls(
            base=RemoteUserManifest(
                author=None,
                timestamp=now,
                id=id or EntryID(),
                version=0,
                created=now,
                updated=now,
                last_processed_message=0,
                workspaces=workspaces,
            ),
            need_sync=True,
            updated=now,
            last_processed_message=0,
            workspaces=workspaces,
        )

    # Helper

    def get_workspace_entry(self, workspace_id: EntryID) -> WorkspaceEntry:
        return next((w for w in self.workspaces if w.id == workspace_id), None)

    # Evolve methods

    def evolve_workspaces_and_mark_updated(self, *data) -> "LocalUserManifest":
        workspaces = {**{w.id: w for w in self.workspaces}, **{w.id: w for w in data}}
        return self.evolve_and_mark_updated(workspaces=tuple(workspaces.values()))

    def evolve_workspaces(self, *data) -> "LocalUserManifest":
        workspaces = {**{w.id: w for w in self.workspaces}, **{w.id: w for w in data}}
        return self.evolve(workspaces=tuple(workspaces.values()))

    # Remote methods

    @classmethod
    def from_remote(cls, remote: RemoteUserManifest) -> "LocalUserManifest":
        return cls(
            base=remote,
            need_sync=False,
            updated=remote.updated,
            last_processed_message=remote.last_processed_message,
            workspaces=remote.workspaces,
        )

    def to_remote(self, author: DeviceID, timestamp: Pendulum = None) -> RemoteUserManifest:
        return RemoteUserManifest(
            author=author,
            timestamp=timestamp or pendulum_now(),
            id=self.id,
            version=self.base_version + 1,
            created=self.created,
            updated=self.updated,
            last_processed_message=self.last_processed_message,
            workspaces=self.workspaces,
        )
