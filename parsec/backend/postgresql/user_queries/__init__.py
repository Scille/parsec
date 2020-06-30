# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.backend.postgresql.user_queries.create import query_create_device, query_create_user
from parsec.backend.postgresql.user_queries.device_invitation import (
    query_cancel_device_invitation,
    query_claim_device_invitation,
    query_create_device_invitation,
    query_get_device_invitation,
)
from parsec.backend.postgresql.user_queries.find import query_find, query_find_humans
from parsec.backend.postgresql.user_queries.get import (
    query_get_user,
    query_get_user_with_device,
    query_get_user_with_device_and_trustchain,
    query_get_user_with_devices_and_trustchain,
    query_get_user_with_trustchain,
)
from parsec.backend.postgresql.user_queries.revoke import query_revoke_user
from parsec.backend.postgresql.user_queries.user_invitation import (
    query_cancel_user_invitation,
    query_claim_user_invitation,
    query_create_user_invitation,
    query_get_user_invitation,
)

__all__ = (
    "query_create_user",
    "query_create_device",
    "query_find",
    "query_find_humans",
    "query_get_user",
    "query_get_user_with_trustchain",
    "query_get_user_with_device_and_trustchain",
    "query_get_user_with_devices_and_trustchain",
    "query_get_user_with_device",
    "query_create_user_invitation",
    "query_get_user_invitation",
    "query_claim_user_invitation",
    "query_cancel_user_invitation",
    "query_create_device_invitation",
    "query_get_device_invitation",
    "query_claim_device_invitation",
    "query_cancel_device_invitation",
    "query_revoke_user",
)
