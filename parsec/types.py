# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import typing
from uuid import UUID, uuid4

K = typing.TypeVar("K")
V = typing.TypeVar("V")


class FrozenDict(dict, typing.Generic[K, V]):
    __slots__ = ()

    def __repr__(self):
        return f"{self.__class__.__name__}({dict.__repr__(self)})"

    def _ro_guard(*args, **kwargs):
        raise AttributeError("FrozenDict doesn't allow modifications")

    __setitem__ = _ro_guard
    __delitem__ = _ro_guard
    pop = _ro_guard
    clear = _ro_guard
    popitem = _ro_guard
    setdefault = _ro_guard
    update = _ro_guard

    def evolve(self, **data):
        return FrozenDict(**self, **data)


class UUID4(UUID):
    __slots__ = ()

    def __init__(self, init=None):
        init = uuid4() if init is None else init
        if isinstance(init, UUID):
            super().__init__(bytes=init.bytes)
        else:
            super().__init__(init)


# Cheap typing
typing.FrozenDict = FrozenDict
