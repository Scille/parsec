# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS
import attr
from typing import Optional
from pendulum import DateTime


@attr.s(frozen=True, slots=True, auto_attribs=True)
class OrganizationStats:
    users: int
    data_size: int
    metadata_size: int


@attr.s(frozen=True, slots=True, auto_attribs=True)
class OrganizationConfig:
    expiration_date: DateTime
    user_profile_outsider_allowed: bool
    users_limit: Optional[int]
