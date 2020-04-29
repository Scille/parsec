# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.api.protocol import packb, user_find_serializer

from tests.common import freeze_time


async def user_find(sock, **kwargs):
    await sock.send(user_find_serializer.req_dumps({"cmd": "user_find", **kwargs}))
    raw_rep = await sock.recv()
    return user_find_serializer.rep_loads(raw_rep)


@pytest.fixture
async def access_testbed(
    backend_factory,
    backend_data_binder_factory,
    backend_sock_factory,
    organization_factory,
    local_device_factory,
):
    async with backend_factory(populated=False) as backend:
        binder = backend_data_binder_factory(backend)
        org = organization_factory("IFD")
        device = local_device_factory("Godfrey@dev1", org)
        with freeze_time("2000-01-01"):
            await binder.bind_organization(org, device)

        async with backend_sock_factory(backend, device) as sock:
            yield binder, org, device, sock


@pytest.mark.trio
async def test_api_user_find_other_organization(
    backend, alice, sock_from_other_organization_factory
):
    # Organizations should be isolated
    async with sock_from_other_organization_factory(backend) as sock:
        rep = await user_find(sock, query=alice.user_id)
        assert rep == {"status": "ok", "results": [], "per_page": 100, "page": 1, "total": 0}


@pytest.mark.trio
async def test_api_user_find(access_testbed, organization_factory, local_device_factory):
    binder, org, godfrey1, sock = access_testbed

    # Populate with cool guys
    for name in ["Philippe@p1", "Philippe@p2", "Mike@p1", "Blacky@p1", "Philip_J_Fry@p1"]:
        device = local_device_factory(name, org)
        await binder.bind_device(device, certifier=godfrey1)

    await binder.bind_revocation("Philip_J_Fry", certifier=godfrey1)

    # Also create homonyme in different organization, just to be sure...
    other_org = organization_factory("FilmMark")
    other_device = local_device_factory("Philippe@p1", other_org)
    await binder.bind_organization(other_org, other_device)

    # # Test exact match
    # rep = await user_find(sock, query="Mike")
    # assert rep == {"status": "ok", "results": ["Mike"], "per_page": 100, "page": 1, "total": 1}

    # Test partial search
    rep = await user_find(sock, query="Phil")
    assert rep == {
        "status": "ok",
        "results": ["Philip_J_Fry", "Philippe"],
        "per_page": 100,
        "page": 1,
        "total": 2,
    }

    # Test case insensitivity
    rep = await user_find(sock, query="phil")
    assert rep == {
        "status": "ok",
        "results": ["Philip_J_Fry", "Philippe"],
        "per_page": 100,
        "page": 1,
        "total": 2,
    }

    # Test partial search while omitting revoked users
    rep = await user_find(sock, query="Phil", omit_revoked=True)
    assert rep == {"status": "ok", "results": ["Philippe"], "per_page": 100, "page": 1, "total": 1}

    # Test partial search with invalid query
    rep = await user_find(sock, query="p*", omit_revoked=True)
    assert rep == {"status": "ok", "results": [], "per_page": 100, "page": 1, "total": 0}

    # Test pagination
    rep = await user_find(sock, query="Phil", page=1, per_page=1)
    assert rep == {
        "status": "ok",
        "results": ["Philip_J_Fry"],
        "per_page": 1,
        "page": 1,
        "total": 2,
    }

    # Test out of pagination
    rep = await user_find(sock, query="Phil", page=2, per_page=5)
    assert rep == {"status": "ok", "results": [], "per_page": 5, "page": 2, "total": 2}

    # Test no params
    rep = await user_find(sock)
    assert rep == {
        "status": "ok",
        "results": ["Blacky", "Godfrey", "Mike", "Philip_J_Fry", "Philippe"],
        "per_page": 100,
        "page": 1,
        "total": 5,
    }

    # Test omit revoked users
    rep = await user_find(sock, omit_revoked=True)
    assert rep == {
        "status": "ok",
        "results": ["Blacky", "Godfrey", "Mike", "Philippe"],
        "per_page": 100,
        "page": 1,
        "total": 4,
    }

    # Test bad params
    for bad in [{"query": 42}, {"page": 0}, {"per_page": 0}, {"per_page": 101}]:
        await sock.send(packb({"cmd": "user_find", **bad}))
        raw_rep = await sock.recv()
        rep = user_find_serializer.rep_loads(raw_rep)
        assert rep["status"] == "bad_message"
