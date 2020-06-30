# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Tuple

from parsec.api.data import BaseData, EntryName
from parsec.serde import BaseSchema, MsgpackSerializer

__all__ = ("BaseLocalData", "FsPath")


class BaseLocalData(BaseData):
    """Unsigned and uncompressed base class for local data"""

    SCHEMA_CLS = BaseSchema
    SERIALIZER_CLS = MsgpackSerializer


class FsPath:
    __slots__ = ("_parts",)
    """
    Represent an absolute path to access a resource in the FS.

    FsPath must be initialized with a str representing an absolute path (i.e.
    with a leading slash). If it countains `.` and/or `..` parts the path will
    be resolved.
    """

    def __init__(self, raw):
        if isinstance(raw, FsPath):
            parts = raw.parts
        elif isinstance(raw, (list, tuple)):
            assert all(isinstance(x, EntryName) for x in raw)
            parts = raw
        else:
            parts = []
            if not raw.startswith("/"):
                raise ValueError("Path must be absolute")

            for raw_part in raw.split("/"):
                if raw_part in (".", ""):
                    continue
                elif raw_part == "..":
                    if parts:
                        parts.pop()
                    continue
                else:
                    parts.append(EntryName(raw_part))

        self._parts = tuple(parts)

    def __str__(self):
        return "/" + "/".join(self._parts)

    def __repr__(self):
        return f"{type(self).__name__}({str(self)!r})"

    def __truediv__(self, entry):
        return type(self)([*self._parts, EntryName(entry)])

    def __eq__(self, other):
        if isinstance(other, FsPath):
            return self._parts == other.parts
        else:
            return NotImplemented

    @property
    def name(self) -> EntryName:
        return self._parts[-1]

    @property
    def parent(self) -> "FsPath":
        return type(self)(self._parts[:-1])

    def is_root(self) -> bool:
        return not self._parts

    @property
    def parts(self) -> Tuple[EntryName, ...]:
        return self._parts
