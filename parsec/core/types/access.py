# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import attr
import pendulum
import functools
from hashlib import sha256
from typing import Optional, Tuple

from parsec.api.protocol import RealmRole, RealmRoleField
from parsec.crypto import SecretKey, HashDigest
from parsec.serde import UnknownCheckedSchema, fields, validate, post_load
from parsec.core.types.base import (
    BlockID,
    BlockIDField,
    ChunkID,
    ChunkIDField,
    EntryID,
    EntryIDField,
    serializer_factory,
    EntryNameField,
)


# Access


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BlockAccess:
    id: BlockID
    key: SecretKey
    offset: int
    size: int
    digest: HashDigest

    @classmethod
    def from_chunk(cls, chunk: "Chunk", digest: str) -> "BlockAccess":
        return cls(
            id=BlockID(chunk.id),
            key=SecretKey.generate(),
            offset=chunk.start,
            size=chunk.stop - chunk.start,
            digest=digest,
        )

    def to_chunk(self) -> "Chunk":
        return Chunk(
            id=ChunkID(self.id),
            raw_offset=self.offset,
            raw_size=self.size,
            start=self.offset,
            stop=self.offset + self.size,
            access=self,
        )


class BlockAccessSchema(UnknownCheckedSchema):
    id = BlockIDField(required=True)
    key = fields.SecretKey(required=True)
    offset = fields.Integer(required=True, validate=validate.Range(min=0))
    size = fields.Integer(required=True, validate=validate.Range(min=0))
    digest = fields.Bytes(required=True, validate=validate.Length(min=1, max=32))

    @post_load
    def make_obj(self, data):
        return BlockAccess(**data)


block_access_serializer = serializer_factory(BlockAccessSchema)


# Chunks


@attr.s(slots=True, frozen=True, auto_attribs=True, cmp=False)
@functools.total_ordering
class Chunk:
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

    id: ChunkID
    start: int
    stop: int
    raw_offset: int
    raw_size: int
    access: Optional[BlockAccess] = None

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
    def new_chunk(cls, start: int, stop: int) -> "Chunk":
        assert start < stop
        return cls(ChunkID(), start, stop, start, stop - start)

    # Evolve

    def evolve(self, **data) -> "Chunk":
        return attr.evolve(self, **data)

    def evolve_as_block(self, data) -> "Chunk":
        # No-op
        if self.is_block:
            return self

        # Check alignement
        if self.raw_offset != self.start:
            raise TypeError("This chunk is not aligned")

        # Craft access
        digest = sha256(data).digest()
        access = BlockAccess.from_chunk(self, digest)

        # Evolve
        return self.evolve(access=access)

    # Export

    def get_block_access(self) -> BlockAccess:
        if not self.is_block:
            raise TypeError("This chunk does not correspond to a block")
        return self.access


class ChunkSchema(UnknownCheckedSchema):
    id = ChunkIDField(required=True)
    start = fields.Integer(required=True, validate=validate.Range(min=0))
    stop = fields.Integer(required=True, validate=validate.Range(min=1))
    raw_offset = fields.Integer(required=True, validate=validate.Range(min=0))
    raw_size = fields.Integer(required=True, validate=validate.Range(min=1))
    access = fields.Nested(BlockAccessSchema, required=True, allow_none=True)

    @post_load
    def make_obj(self, data):
        return Chunk(**data)


chunk_serializer = serializer_factory(ChunkSchema)

Chunks = Tuple[Chunk, ...]


# Workspaces

# Republishing under a better name
WorkspaceRole = RealmRole
WorkspaceRoleField = RealmRoleField


# Not stricly speaking an access, but close enough...


@attr.s(slots=True, frozen=True, auto_attribs=True)
class WorkspaceEntry:
    name: str
    id: EntryID = attr.ib(factory=EntryID)
    key: SecretKey = attr.ib(factory=SecretKey.generate)
    encryption_revision: int = 1
    encrypted_on: pendulum.Pendulum = attr.ib(factory=pendulum.now)
    role_cached_on: pendulum.Pendulum = attr.ib(factory=pendulum.now)
    role: Optional[WorkspaceRole] = WorkspaceRole.OWNER

    def is_revoked(self) -> bool:
        return self.role is None

    def evolve(self, **kwargs) -> "WorkspaceEntry":
        return attr.evolve(self, **kwargs)

    def evolve_and_mark_updated(self, **data) -> "WorkspaceEntry":
        if "role_cached_on" not in data:
            data["role_cached_on"] = pendulum.now()
        return attr.evolve(self, **data)


class WorkspaceEntrySchema(UnknownCheckedSchema):
    name = EntryNameField(validate=validate.Length(min=1, max=256), required=True)
    id = EntryIDField(required=True)
    key = fields.SecretKey(required=True)
    encryption_revision = fields.Int(required=True, validate=validate.Range(min=0))
    encrypted_on = fields.DateTime(required=True)
    role_cached_on = fields.DateTime(required=True)
    role = WorkspaceRoleField(required=True, allow_none=True)

    @post_load
    def make_obj(self, data):
        return WorkspaceEntry(**data)


workspace_entry_serializer = serializer_factory(WorkspaceEntrySchema)
