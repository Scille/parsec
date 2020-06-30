# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID, uuid4

from parsec.serde import fields

__all__ = ("EntryID", "EntryIDField", "EntryName", "EntryNameField")


class EntryID(UUID):
    __slots__ = ()

    def __init__(self, init=None):
        init = uuid4() if init is None else init
        if isinstance(init, UUID):
            super().__init__(bytes=init.bytes)
        elif isinstance(init, bytes):
            super().__init__(bytes=init)
        else:
            super().__init__(hex=init)


EntryIDField = fields.uuid_based_field_factory(EntryID)


class EntryName(str):
    __slots__ = ()

    def __init__(self, raw):

        # Stick to UNIX filesystem philosophy:
        # - no `.` or `..` name
        # - no `/` or null byte in the name
        # - max 255 bytes long name
        if (
            not isinstance(raw, str)
            or not 0 < len(raw.encode("utf8")) < 256
            or raw == "."
            or raw == ".."
            or "/" in raw
            or "\x00" in raw
        ):
            raise ValueError("Invalid entry name")


EntryNameField = fields.str_based_field_factory(EntryName)
