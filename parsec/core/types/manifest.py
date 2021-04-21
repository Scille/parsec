# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import attr
import functools
from typing import Optional, Tuple, TypeVar, Type, Union, NoReturn, FrozenSet, Pattern, Dict
from parsec.datetime import DateTime, now as datetime_now

from parsec.types import UUID4, FrozenDict
from parsec.crypto import SecretKey, HashDigest
from parsec.serde import fields, OneOfSchema, validate, post_load
from parsec.api.protocol import DeviceID, RealmRole
from parsec.api.data import (
    BaseSchema,
    BaseData,
    WorkspaceEntry,
    BlockAccess,
    BlockID,
    BaseManifest as BaseRemoteManifest,
    UserManifest as RemoteUserManifest,
    WorkspaceManifest as RemoteWorkspaceManifest,
    FolderManifest as RemoteFolderManifest,
    FileManifest as RemoteFileManifest,
    EntryID,
    EntryName,
    EntryNameField,
    EntryIDField,
)
from parsec.core.types.base import BaseLocalData
from enum import Enum

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
@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
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

    def get_block_access(self) -> Optional[BlockAccess]:
        if not self.is_block:
            raise TypeError("This chunk does not correspond to a block")
        return self.access


# Manifests data classes


class LocalManifestType(Enum):
    LOCAL_FILE_MANIFEST = "local_file_manifest"
    LOCAL_FOLDER_MANIFEST = "local_folder_manifest"
    LOCAL_WORKSPACE_MANIFEST = "local_workspace_manifest"
    LOCAL_USER_MANIFEST = "local_user_manifest"


LocalManifestTypeVar = TypeVar("LocalManifestTypeVar", bound="BaseLocalManifest")
LocalFileManifestTypeVar = TypeVar("LocalFileManifestTypeVar", bound="LocalFileManifest")
LocalFolderManifestTypeVar = TypeVar("LocalFolderManifestTypeVar", bound="LocalFolderManifest")
LocalWorkspaceManifestTypeVar = TypeVar(
    "LocalWorkspaceManifestTypeVar", bound="LocalWorkspaceManifest"
)
LocalUserManifestTypeVar = TypeVar("LocalUserManifestTypeVar", bound="LocalUserManifest")
LocalManifestsTypeVar = Union[
    LocalManifestTypeVar,
    LocalFileManifestTypeVar,
    LocalFolderManifestTypeVar,
    LocalWorkspaceManifestTypeVar,
    LocalUserManifestTypeVar,
]


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class BaseLocalManifest(BaseLocalData):
    class SCHEMA_CLS(OneOfSchema, BaseSchema):
        type_field = "type"
        base = fields.Nested(BaseRemoteManifest.SCHEMA_CLS, required=True)
        need_sync = fields.Boolean(required=True)
        updated = fields.DateTime(required=True)

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

    need_sync: bool
    updated: DateTime
    base: BaseRemoteManifest  # base must be overwritten in subclass

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

    def evolve_and_mark_updated(self: LocalManifestTypeVar, **data) -> LocalManifestTypeVar:
        if "updated" not in data:
            data["updated"] = datetime_now()
        data.setdefault("need_sync", True)
        return self.evolve(**data)

    # Remote methods

    @classmethod
    def from_remote(
        cls: Type[LocalManifestTypeVar], remote: BaseRemoteManifest, prevent_sync_pattern: Pattern
    ) -> LocalManifestsTypeVar:
        if isinstance(remote, RemoteFileManifest):
            return LocalFileManifest.from_remote(remote)
        elif isinstance(remote, RemoteFolderManifest):
            return LocalFolderManifest.from_remote(remote, prevent_sync_pattern)
        elif isinstance(remote, RemoteWorkspaceManifest):
            return LocalWorkspaceManifest.from_remote(remote, prevent_sync_pattern)
        elif isinstance(remote, RemoteUserManifest):
            return LocalUserManifest.from_remote(remote)
        raise ValueError("Wrong remote type")

    @classmethod
    def from_remote_with_local_context(
        cls: Type[LocalManifestTypeVar],
        remote: BaseRemoteManifest,
        prevent_sync_pattern: Pattern,
        local_manifest: LocalManifestTypeVar,
    ) -> LocalManifestsTypeVar:
        if isinstance(remote, RemoteFileManifest):
            return LocalFileManifest.from_remote(remote)
        elif isinstance(remote, RemoteFolderManifest):
            return LocalFolderManifest.from_remote_with_local_context(
                remote, prevent_sync_pattern, local_manifest
            )
        elif isinstance(remote, RemoteWorkspaceManifest):
            return LocalWorkspaceManifest.from_remote_with_local_context(
                remote, prevent_sync_pattern, local_manifest
            )
        elif isinstance(remote, RemoteUserManifest):
            return LocalUserManifest.from_remote(remote)
        raise ValueError("Wrong remote type")

    def to_remote(self, author: Optional[DeviceID], timestamp: DateTime = None) -> NoReturn:
        raise NotImplementedError

    def match_remote(self, remote_manifest: BaseRemoteManifest) -> bool:
        reference: BaseRemoteManifest = self.to_remote(
            author=remote_manifest.author, timestamp=remote_manifest.timestamp
        )
        return reference.evolve(version=remote_manifest.version) == remote_manifest

    # Stat method

    def to_stats(self):
        # General stats
        stats = {
            "id": self.id,
            "created": self.created,
            "updated": self.updated,
            "base_version": self.base_version,
            "is_placeholder": self.is_placeholder,
            "need_sync": self.need_sync,
        }
        return stats

    # Debugging

    def asdict(self):
        dct = attr.asdict(self)
        dct.pop("base")
        for name in "base_version", "is_placeholder", "created":
            dct[name] = getattr(self, name)
        if hasattr(self.base, "parent"):
            dct["parent"] = self.parent
        return dct


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class LocalFileManifest(BaseLocalManifest):
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
    size: int
    blocksize: int
    blocks: Tuple[Tuple[Chunk, ...], ...]

    @classmethod
    def new_placeholder(
        cls,
        author: DeviceID,
        parent: EntryID,
        id: Optional[EntryID] = None,
        now: DateTime = None,
        blocksize=DEFAULT_BLOCK_SIZE,
    ) -> "LocalFileManifest":
        now = now or datetime_now()
        blocks = ()
        return cls(
            base=RemoteFileManifest(
                author=author,
                timestamp=now,
                id=id or EntryID.new(),
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

    def to_stats(self):
        stats = super().to_stats()
        stats["type"] = "file"
        stats["size"] = self.size
        return stats

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
    def from_remote(
        cls: Type[LocalFileManifestTypeVar], remote: RemoteFileManifest
    ) -> LocalFileManifestTypeVar:
        return cls(
            base=remote,
            need_sync=False,
            updated=remote.updated,
            size=remote.size,
            blocksize=remote.blocksize,
            blocks=tuple((Chunk.from_block_acess(block_access),) for block_access in remote.blocks),
        )

    def to_remote(self, author: DeviceID, timestamp: DateTime = None) -> RemoteFileManifest:
        # Checks
        self.assert_integrity()
        assert self.is_reshaped()

        # Blocks
        blocks = tuple(chunks[0].get_block_access() for chunks in self.blocks)

        return RemoteFileManifest(
            author=author,
            timestamp=timestamp or datetime_now(),
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


LocalFolderishManifestTypeVar = TypeVar("LocalManifestTypeVar", bound="LocalFolderishManifestMixin")


class LocalFolderishManifestMixin:
    """Common methods for LocalFolderManifest and LocalWorkspaceManifest"""

    # Evolve methods

    def evolve_children_and_mark_updated(
        self: LocalFolderishManifestTypeVar,
        data: Dict[str, Optional[EntryID]],
        prevent_sync_pattern: Pattern,
    ) -> LocalFolderishManifestTypeVar:
        updated = False
        new_children = dict(self.children)
        new_local_confinement_points = set(self.local_confinement_points)

        # Deal with removal first
        for name, entry_id in data.items():
            if name not in new_children:
                continue
            # Remove old entry
            old_entry_id = new_children.pop(name)
            if old_entry_id in new_local_confinement_points:
                new_local_confinement_points.discard(old_entry_id)
            else:
                updated = True

        # Make sure no entry_id is duplicated
        assert not set(data.values()).intersection(new_children.values())

        # Deal with additions second
        for name, entry_id in data.items():
            if entry_id is None:
                continue
            # Add new entry
            new_children[name] = entry_id
            if prevent_sync_pattern.match(name):
                new_local_confinement_points.add(entry_id)
            else:
                updated = True

        new_local_confinement_points = frozenset(new_local_confinement_points)
        if not updated:
            return self.evolve(
                children=new_children, local_confinement_points=new_local_confinement_points
            )
        return self.evolve_and_mark_updated(
            children=new_children, local_confinement_points=new_local_confinement_points
        )

    # Filtering and confinement helpers

    def _filter_local_confinement_points(
        self: LocalFolderishManifestTypeVar
    ) -> LocalFolderishManifestTypeVar:
        if not self.local_confinement_points:
            return self
        children = {
            name: entry_id
            for name, entry_id in self.children.items()
            if entry_id not in self.local_confinement_points
        }
        return self.evolve(local_confinement_points=frozenset(), children=children)

    def _restore_local_confinement_points(
        self: LocalFolderishManifestTypeVar,
        other: LocalFolderishManifestTypeVar,
        prevent_sync_pattern: Pattern,
    ) -> LocalFolderishManifestTypeVar:
        # Using self.remote_confinement_points is useful to restore entries that were present locally
        # before applying a new filter that filtered those entries from the remote manifest
        if not other.local_confinement_points and not self.remote_confinement_points:
            return self
        previously_local_confinement_points = {
            name: entry_id
            for name, entry_id in other.children.items()
            if entry_id in other.local_confinement_points
            or entry_id in self.remote_confinement_points
        }
        return self.evolve_children_and_mark_updated(
            previously_local_confinement_points, prevent_sync_pattern
        )

    def _filter_remote_entries(
        self: LocalFolderishManifestTypeVar, prevent_sync_pattern: Pattern
    ) -> LocalFolderishManifestTypeVar:
        remote_confinement_points = frozenset(
            {
                entry_id
                for name, entry_id in self.children.items()
                if prevent_sync_pattern.match(name)
            }
        )
        if not remote_confinement_points:
            return self
        children = {
            name: entry_id
            for name, entry_id in self.children.items()
            if entry_id not in remote_confinement_points
        }
        return self.evolve(children=children, remote_confinement_points=remote_confinement_points)

    def _restore_remote_confinement_points(
        self: LocalFolderishManifestTypeVar
    ) -> LocalFolderishManifestTypeVar:
        if not self.remote_confinement_points:
            return self
        children = dict(self.children)
        for name, entry_id in self.base.children.items():
            if entry_id in self.remote_confinement_points:
                children[name] = entry_id
        return self.evolve(remote_confinement_points=frozenset(), children=children)

    # Apply "prevent sync" pattern

    def apply_prevent_sync_pattern(
        self: LocalFolderishManifestTypeVar, prevent_sync_pattern: Pattern
    ) -> LocalFolderishManifestTypeVar:
        # Filter local confinement points
        result = self._filter_local_confinement_points()
        # Restore remote confinement points
        result = result._restore_remote_confinement_points()
        # Filter remote confinement_points
        result = result._filter_remote_entries(prevent_sync_pattern)
        # Restore local confinement points
        return result._restore_local_confinement_points(self, prevent_sync_pattern)


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class LocalFolderManifest(BaseLocalManifest, LocalFolderishManifestMixin):
    class SCHEMA_CLS(BaseSchema):
        type = fields.EnumCheckedConstant(LocalManifestType.LOCAL_FOLDER_MANIFEST, required=True)
        base = fields.Nested(RemoteFolderManifest.SCHEMA_CLS, required=True)
        need_sync = fields.Boolean(required=True)
        updated = fields.DateTime(required=True)
        children = fields.FrozenMap(EntryNameField(), EntryIDField(required=True), required=True)
        # Confined entries are entries that are meant to stay locally and not be added
        # to the uploaded remote manifest when synchronizing. The criteria for being
        # confined is to have a filename that matched the "prevent sync" pattern at the time of
        # the last change (or when a new filter was successfully applied)
        local_confinement_points = fields.FrozenSet(EntryIDField(required=True))

        # Filtered entries are entries present in the base manifest that are not exposed
        # locally. We keep track of them to remember that those entries have not been
        # deleted locally and hence should be restored when crafting the remote manifest
        # to upload.
        remote_confinement_points = fields.FrozenSet(EntryIDField(required=True))

        @post_load
        def make_obj(self, data):
            data.pop("type")
            data.setdefault("local_confinement_points", frozenset())
            data.setdefault("remote_confinement_points", frozenset())
            return LocalFolderManifest(**data)

    base: RemoteFolderManifest
    children: FrozenDict[EntryName, EntryID]
    local_confinement_points: FrozenSet[EntryID]
    remote_confinement_points: FrozenSet[EntryID]

    @classmethod
    def new_placeholder(
        cls, author: DeviceID, parent: EntryID, id: EntryID = None, now: DateTime = None
    ) -> "LocalFolderManifest":
        now = now or datetime_now()
        children = FrozenDict()
        return cls(
            base=RemoteFolderManifest(
                author=author,
                timestamp=now,
                id=id or EntryID.new(),
                parent=parent,
                version=0,
                created=now,
                updated=now,
                children=children,
            ),
            need_sync=True,
            updated=now,
            children=children,
            local_confinement_points=frozenset(),
            remote_confinement_points=frozenset(),
        )

    # Properties

    def to_stats(self):
        stats = super().to_stats()
        stats["type"] = "folder"
        stats["children"] = sorted(self.children.keys())
        return stats

    @property
    def parent(self):
        return self.base.parent

    # Remote methods

    @classmethod
    def from_remote(
        cls, remote: RemoteFolderManifest, prevent_sync_pattern: Pattern
    ) -> "LocalFolderManifest":
        # Create local manifest
        result = cls(
            base=remote,
            need_sync=False,
            updated=remote.updated,
            children=remote.children,
            local_confinement_points=frozenset(),
            remote_confinement_points=frozenset(),
        )
        # Filter remote entries
        return result._filter_remote_entries(prevent_sync_pattern)

    @classmethod
    def from_remote_with_local_context(
        cls,
        remote: RemoteFolderManifest,
        prevent_sync_pattern: Pattern,
        local_manifest: "LocalFolderManifest",
    ) -> "LocalFolderManifest":
        result = cls.from_remote(remote, prevent_sync_pattern)
        return result._restore_local_confinement_points(local_manifest, prevent_sync_pattern)

    def to_remote(self, author: DeviceID, timestamp: DateTime = None) -> RemoteFolderManifest:
        # Filter confined entries
        processed_manifest = self._filter_local_confinement_points()
        # Restore filtered entries
        processed_manifest = processed_manifest._restore_remote_confinement_points()
        # Create remote manifest
        return RemoteFolderManifest(
            author=author,
            timestamp=timestamp or datetime_now(),
            id=self.id,
            parent=self.parent,
            version=self.base_version + 1,
            created=self.created,
            updated=self.updated,
            children=processed_manifest.children,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class LocalWorkspaceManifest(BaseLocalManifest, LocalFolderishManifestMixin):
    class SCHEMA_CLS(BaseSchema):
        type = fields.EnumCheckedConstant(LocalManifestType.LOCAL_WORKSPACE_MANIFEST, required=True)
        base = fields.Nested(RemoteWorkspaceManifest.SCHEMA_CLS, required=True)
        need_sync = fields.Boolean(required=True)
        updated = fields.DateTime(required=True)
        children = fields.FrozenMap(EntryNameField(), EntryIDField(required=True), required=True)
        # Confined entries are entries that are meant to stay locally and not be added
        # to the uploaded remote manifest when synchronizing. The criteria for being
        # confined is to have a filename that matched the "prevent sync" pattern at the time of
        # the last change (or when a new filter was successfully applied)
        local_confinement_points = fields.FrozenSet(EntryIDField(required=True))
        # Filtered entries are entries present in the base manifest that are not exposed
        # locally. We keep track of them to remember that those entries have not been
        # deleted locally and hence should be restored when crafting the remote manifest
        # to upload.
        remote_confinement_points = fields.FrozenSet(EntryIDField(required=True))

        @post_load
        def make_obj(self, data):
            data.pop("type")
            data.setdefault("local_confinement_points", frozenset())
            data.setdefault("remote_confinement_points", frozenset())
            return LocalWorkspaceManifest(**data)

    base: RemoteWorkspaceManifest
    children: FrozenDict[EntryName, EntryID]
    local_confinement_points: FrozenSet[EntryID]
    remote_confinement_points: FrozenSet[EntryID]

    @classmethod
    def new_placeholder(
        cls, author: DeviceID, id: EntryID = None, now: DateTime = None
    ) -> "LocalWorkspaceManifest":
        now = now or datetime_now()
        children = FrozenDict()
        return cls(
            base=RemoteWorkspaceManifest(
                author=author,
                timestamp=now,
                id=id or EntryID.new(),
                version=0,
                created=now,
                updated=now,
                children=children,
            ),
            need_sync=True,
            updated=now,
            children=children,
            local_confinement_points=frozenset(),
            remote_confinement_points=frozenset(),
        )

    # Evolve methods

    def to_stats(self):
        stats = super().to_stats()
        stats["type"] = "folder"
        stats["children"] = sorted(self.children.keys())
        return stats

    # Remote methods

    @classmethod
    def from_remote(
        cls, remote: RemoteFolderManifest, prevent_sync_pattern: Pattern
    ) -> "LocalWorkspaceManifest":
        # Create local manifest
        result = cls(
            base=remote,
            need_sync=False,
            updated=remote.updated,
            children=remote.children,
            local_confinement_points=frozenset(),
            remote_confinement_points=frozenset(),
        )
        # Filter remote entries
        return result._filter_remote_entries(prevent_sync_pattern)

    @classmethod
    def from_remote_with_local_context(
        cls,
        remote: RemoteFolderManifest,
        prevent_sync_pattern: Pattern,
        local_manifest: "LocalWorkspaceManifest",
    ) -> "LocalWorkspaceManifest":
        result = cls.from_remote(remote, prevent_sync_pattern)
        return result._restore_local_confinement_points(local_manifest, prevent_sync_pattern)

    def to_remote(self, author: DeviceID, timestamp: DateTime = None) -> RemoteWorkspaceManifest:
        # Filter confined entries
        processed_manifest = self._filter_local_confinement_points()
        # Restore filtered entries
        processed_manifest = processed_manifest._restore_remote_confinement_points()
        # Create remote manifest
        return RemoteWorkspaceManifest(
            author=author,
            timestamp=timestamp or datetime_now(),
            id=self.id,
            version=self.base_version + 1,
            created=self.created,
            updated=self.updated,
            children=processed_manifest.children,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True, kw_only=True, eq=False)
class LocalUserManifest(BaseLocalManifest):
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

    last_processed_message: int
    workspaces: Tuple[WorkspaceEntry, ...]

    @classmethod
    def new_placeholder(
        cls, author: DeviceID, id: EntryID = None, now: DateTime = None
    ) -> "LocalUserManifest":
        workspaces = ()
        now = now or datetime_now()
        return cls(
            base=RemoteUserManifest(
                author=author,
                timestamp=now,
                id=id or EntryID.new(),
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

    def to_remote(self, author: DeviceID, timestamp: DateTime = None) -> RemoteUserManifest:
        return RemoteUserManifest(
            author=author,
            timestamp=timestamp or datetime_now(),
            id=self.id,
            version=self.base_version + 1,
            created=self.created,
            updated=self.updated,
            last_processed_message=self.last_processed_message,
            workspaces=self.workspaces,
        )
