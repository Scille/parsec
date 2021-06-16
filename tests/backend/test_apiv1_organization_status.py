# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from typing import Dict
from unittest.mock import ANY
from pendulum import datetime
import pytest

from parsec.api.protocol import (
    apiv1_organization_status_serializer,
    apiv1_organization_update_serializer,
)
from tests.backend.test_apiv1_organization import organization_create
from parsec.backend.backend_events import BackendEvent


async def organization_status(sock, organization_id):
    raw_rep = await sock.send(
        apiv1_organization_status_serializer.req_dumps(
            {"cmd": "organization_status", "organization_id": organization_id}
        )
    )
    raw_rep = await sock.recv()
    return apiv1_organization_status_serializer.rep_loads(raw_rep)


async def organization_update(sock, organization_id, **fields: Dict):
    raw_rep = await sock.send(
        apiv1_organization_update_serializer.req_dumps(
            {"cmd": "organization_update", "organization_id": organization_id, **fields}
        )
    )
    raw_rep = await sock.recv()
    return apiv1_organization_update_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_organization_status_bootstrapped(coolorg, administration_backend_sock, backend):
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }


@pytest.mark.trio
async def test_organization_status_not_bootstrapped(
    organization_factory, administration_backend_sock, backend
):
    # 1) Create organization
    neworg = organization_factory("NewOrg")
    rep = await organization_create(administration_backend_sock, neworg.organization_id)
    assert rep == {
        "status": "ok",
        "bootstrap_token": ANY,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    # 2) Check its status
    rep = await organization_status(administration_backend_sock, neworg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": False,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }


@pytest.mark.trio
async def test_organization_update_only_expiration_date(
    coolorg, organization_factory, administration_backend_sock, backend
):
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    rep = await organization_update(
        administration_backend_sock, coolorg.organization_id, expiration_date=datetime(2077, 1, 1)
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": datetime(2077, 1, 1),
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    rep = await organization_update(
        administration_backend_sock, coolorg.organization_id, expiration_date=None
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }


@pytest.mark.trio
async def test_organization_update_expiration_date_with_expired_event(
    coolorg, organization_factory, administration_backend_sock, backend
):
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    # New expired organization
    with backend.event_bus.listen() as spy:
        rep = await organization_update(
            administration_backend_sock,
            coolorg.organization_id,
            expiration_date=datetime(1999, 12, 31),
        )
        assert rep == {"status": "ok"}
        await spy.wait_with_timeout(BackendEvent.ORGANIZATION_EXPIRED)

        rep = await organization_status(administration_backend_sock, coolorg.organization_id)
        assert rep == {
            "status": "ok",
            "is_bootstrapped": True,
            "expiration_date": datetime(1999, 12, 31),
            "user_profile_outsider_allowed": False,
            "users_limit": backend.config.organization_config.default_users_limit,
        }

    # Already Expired organization
    with backend.event_bus.listen() as spy:
        rep = await organization_update(
            administration_backend_sock,
            coolorg.organization_id,
            expiration_date=datetime(2000, 1, 31),
        )
        assert rep == {"status": "ok"}
        await spy.wait_with_timeout(BackendEvent.ORGANIZATION_EXPIRED)
        rep = await organization_status(administration_backend_sock, coolorg.organization_id)
        assert rep == {
            "status": "ok",
            "is_bootstrapped": True,
            "expiration_date": datetime(2000, 1, 31),
            "user_profile_outsider_allowed": False,
            "users_limit": backend.config.organization_config.default_users_limit,
        }


@pytest.mark.trio
async def test_organization_update_only_user_profile_outsider_allowed(
    coolorg, organization_factory, administration_backend_sock, backend
):
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    rep = await organization_update(
        administration_backend_sock, coolorg.organization_id, user_profile_outsider_allowed=True
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": True,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    rep = await organization_update(
        administration_backend_sock, coolorg.organization_id, user_profile_outsider_allowed=False
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }


@pytest.mark.trio
async def test_organization_update_only_users_limit(
    coolorg, organization_factory, administration_backend_sock, backend
):
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    rep = await organization_update(
        administration_backend_sock, coolorg.organization_id, users_limit=25
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": 25,
    }

    rep = await organization_update(
        administration_backend_sock, coolorg.organization_id, users_limit=None
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": None,
    }


@pytest.mark.trio
async def test_organization_update_multiple_fields(
    coolorg, organization_factory, administration_backend_sock, backend
):
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": False,
        "users_limit": backend.config.organization_config.default_users_limit,
    }

    rep = await organization_update(
        administration_backend_sock,
        coolorg.organization_id,
        expiration_date=datetime(2077, 1, 1),
        user_profile_outsider_allowed=True,
        users_limit=5689,
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": datetime(2077, 1, 1),
        "user_profile_outsider_allowed": True,
        "users_limit": 5689,
    }

    rep = await organization_update(
        administration_backend_sock,
        coolorg.organization_id,
        expiration_date=None,
        user_profile_outsider_allowed=True,
        users_limit=None,
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": None,
        "user_profile_outsider_allowed": True,
        "users_limit": None,
    }

    rep = await organization_update(
        administration_backend_sock,
        coolorg.organization_id,
        expiration_date=datetime(2077, 1, 1),
        user_profile_outsider_allowed=False,
        users_limit=3,
    )
    assert rep == {"status": "ok"}
    rep = await organization_status(administration_backend_sock, coolorg.organization_id)
    assert rep == {
        "status": "ok",
        "is_bootstrapped": True,
        "expiration_date": datetime(2077, 1, 1),
        "user_profile_outsider_allowed": False,
        "users_limit": 3,
    }


@pytest.mark.trio
async def test_organization_update_unknown_organization(
    coolorg, organization_factory, administration_backend_sock
):
    rep = await organization_update(
        administration_backend_sock, "dummy", expiration_date=datetime(2077, 1, 1)
    )
    assert rep == {"status": "not_found"}

    rep = await organization_update(
        administration_backend_sock, "dummy", user_profile_outsider_allowed=False
    )
    assert rep == {"status": "not_found"}

    rep = await organization_update(
        administration_backend_sock,
        "dummy",
        user_profile_outsider_allowed=False,
        expiration_date=datetime(2077, 1, 1),
    )
    assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_status_unknown_organization(administration_backend_sock):
    rep = await organization_status(administration_backend_sock, organization_id="dummy")
    assert rep == {"status": "not_found"}
