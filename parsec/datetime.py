# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import re
from contextlib import contextmanager
from datetime import datetime as _datetime, timezone, timedelta
from typing import Optional, Callable


__all__ = ("timedelta", "DateTime", "now", "from_timestamp", "from_isoformat", "test_freeze_time")


class DateTime:
    __slots__ = "_dt"

    def __init__(self, year, month, day, hour=0, minute=0, second=0, microsecond=0):
        self._dt = _datetime(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            microsecond=microsecond,
            tzinfo=timezone.utc,
        )

    def isoformat(self) -> str:
        dt = self._dt
        return f"{dt.year}-{dt.month:0>2}-{dt.day:0>2}T{dt.hour:0>2}:{dt.minute:0>2}:{dt.second:0>2}.{dt.microsecond:0>6}Z"

    def __repr__(self):
        return f"DateTime({self.isoformat()})"

    def __str__(self):
        return self.isoformat()

    def __hash__(self):
        return self._dt.__hash__()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DateTime):
            return self._dt == other._dt
        # Allows to correctly check against unittest.mock.ANY
        return self._dt == other

    def __lt__(self, other):
        if not isinstance(other, DateTime):
            return NotImplemented
        return self._dt.__lt__(other._dt)

    def __le__(self, other):
        if not isinstance(other, DateTime):
            return NotImplemented
        return self._dt.__le__(other._dt)

    def __gt__(self, other):
        if not isinstance(other, DateTime):
            return NotImplemented
        return self._dt.__gt__(other._dt)

    def __ge__(self, other):
        if not isinstance(other, DateTime):
            return NotImplemented
        return self._dt.__ge__(other._dt)

    def __sub__(self, other: object):
        if isinstance(other, DateTime):
            return self._dt - other._dt  # Returns timedelta
        elif isinstance(other, timedelta):
            ret = DateTime.__new__(DateTime)  # Skip __init__
            ret._dt = self._dt - other
            return ret
        else:
            return NotImplemented

    def __add__(self, other: object):
        if isinstance(other, timedelta):
            ret = DateTime.__new__(DateTime)  # Skip __init__
            ret._dt = self._dt + other
            return ret
        else:
            return NotImplemented

    def add(
        self,
        *,
        days: int = 0,
        seconds: int = 0,
        microseconds: int = 0,
        milliseconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        weeks: int = 0,
    ) -> "DateTime":
        ret = DateTime.__new__(DateTime)  # Skip __init__
        ret._dt = self._dt + timedelta(
            days=days,
            seconds=seconds,
            microseconds=microseconds,
            milliseconds=milliseconds,
            minutes=minutes,
            hours=hours,
            weeks=weeks,
        )
        return ret

    @property
    def year(self):
        return self._dt.year

    @property
    def month(self):
        return self._dt.month

    @property
    def day(self):
        return self._dt.day

    @property
    def hour(self):
        return self._dt.hour

    @property
    def minute(self):
        return self._dt.minute

    @property
    def second(self):
        return self._dt.second

    @property
    def microsecond(self):
        return self._dt.microsecond

    @property
    def tzinfo(self):
        return self._dt.tzinfo

    def timestamp(self) -> int:
        return self._dt.timestamp()


_now_hook: Optional[Callable[[], DateTime]] = None


def now() -> DateTime:
    if _now_hook:
        return _now_hook()
    self = DateTime.__new__(DateTime)  # Skip __init__
    self._dt = _datetime.now(tz=timezone.utc)
    return self


def from_timestamp(ts: int) -> DateTime:
    self = DateTime.__new__(DateTime)  # Skip __init__
    self._dt = _datetime.fromtimestamp(ts, tz=timezone.utc)
    return self


ISOFORMAT_REGEX = re.compile(
    r"^(?P<year>[0-9]+)-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})(T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})(.(?P<microseconds>[0-9]{1,6}))?Z)?$"
)


def from_isoformat(raw: str) -> DateTime:
    match = ISOFORMAT_REGEX.match(raw)
    if not match:
        raise ValueError(
            "Invalid datetime, allowd formats are: `2000-01-01`, `2000-01-01T00:00:00Z` or `2000-01-01T00:00:00.000Z`"
        )
    return DateTime(
        year=int(match.group("year") or 0),
        month=int(match.group("month") or 0),
        day=int(match.group("day") or 0),
        hour=int(match.group("hour") or 0),
        minute=int(match.group("minute") or 0),
        second=int(match.group("second") or 0),
        microsecond=int(f"{(match.group('microseconds') or 0):0<6}"),
    )


def from_localtime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> DateTime:
    # Generate timezone-naive datetime with local time
    local_dt = _datetime(
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=second,
        microsecond=microsecond,
        tzinfo=timezone.utc,
    )
    return from_timestamp(local_dt.timestamp())


@contextmanager
def test_freeze_time(dt: DateTime):
    global _now_hook
    previous_hook = _now_hook

    def freeze_time_hook():
        # No need to copy dt here given datetime is guaranteed to be immutable
        return dt

    _now_hook = freeze_time_hook
    yield

    assert _now_hook is freeze_time_hook
    _now_hook = previous_hook
