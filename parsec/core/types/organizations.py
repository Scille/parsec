# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS
import attr
from typing import Optional
from pendulum import DateTime
from typing import List
from parsec.api.protocol import UsersPerProfileDetailItemSchema


@attr.s(frozen=True, slots=True, auto_attribs=True)
class OrganizationStats:
    users: int
    active_users: int
    data_size: int
    metadata_size: int
    users_per_profile_detail: List[UsersPerProfileDetailItemSchema]


@attr.s(frozen=True, slots=True, auto_attribs=True)
class OrganizationConfig:
    expiration_date: DateTime
    user_profile_outsider_allowed: bool
    active_users_limit: Optional[int] = None
