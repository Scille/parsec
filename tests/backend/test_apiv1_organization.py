# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pendulum
import pytest

from parsec.api.data import DeviceCertificateContent, UserCertificateContent, UserProfile
from parsec.api.protocol import (
    UserID,
    apiv1_organization_bootstrap_serializer,
    apiv1_organization_create_serializer,
)
from parsec.api.protocol.handshake import HandshakeOrganizationExpired
from tests.backend.common import ping
from tests.common import freeze_time
from tests.fixtures import local_device_to_backend_user


async def organization_create(sock, organization_id, expiration_date=None):
    req = {"cmd": "organization_create", "organization_id": organization_id}
    if expiration_date:
        req["expiration_date"] = expiration_date
    raw_rep = await sock.send(apiv1_organization_create_serializer.req_dumps(req))
    raw_rep = await sock.recv()
    return apiv1_organization_create_serializer.rep_loads(raw_rep)


_missing = object()


async def organization_bootstrap(
    sock,
    bootstrap_token,
    user_certificate,
    device_certificate,
    root_verify_key,
    redacted_user_certificate=_missing,
    redacted_device_certificate=_missing,
):
    data = {
        "cmd": "organization_bootstrap",
        "bootstrap_token": bootstrap_token,
        "user_certificate": user_certificate,
        "device_certificate": device_certificate,
        "root_verify_key": root_verify_key,
    }

    if redacted_user_certificate is not _missing:
        data["redacted_user_certificate"] = redacted_user_certificate
    if redacted_device_certificate is not _missing:
        data["redacted_device_certificate"] = redacted_device_certificate

    raw_rep = await sock.send(apiv1_organization_bootstrap_serializer.req_dumps(data))
    raw_rep = await sock.recv()
    return apiv1_organization_bootstrap_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_organization_create_already_exists(administration_backend_sock, coolorg):
    rep = await organization_create(administration_backend_sock, coolorg.organization_id)
    assert rep["status"] == "already_exists"


@pytest.mark.trio
async def test_organization_create_bad_name(administration_backend_sock):
    rep = await organization_create(administration_backend_sock, "a" * 33)
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_organization_create_wrong_expiration_date(administration_backend_sock):
    rep = await organization_create(administration_backend_sock, "new", "2010-01-01")
    assert rep["status"] == "bad_message"


@pytest.mark.trio
async def test_organization_recreate_and_bootstrap(
    backend,
    organization_factory,
    local_device_factory,
    administration_backend_sock,
    apiv1_backend_sock_factory,
):
    neworg = organization_factory("NewOrg")
    rep = await organization_create(administration_backend_sock, neworg.organization_id)
    assert rep == {"status": "ok", "bootstrap_token": ANY}
    bootstrap_token1 = rep["bootstrap_token"]

    # Can recreate the organization as long as it hasn't been bootstrapped yet
    rep = await organization_create(administration_backend_sock, neworg.organization_id)
    assert rep == {"status": "ok", "bootstrap_token": ANY}
    bootstrap_token2 = rep["bootstrap_token"]

    assert bootstrap_token1 != bootstrap_token2

    newalice = local_device_factory(org=neworg, profile=UserProfile.ADMIN)
    backend_newalice, backend_newalice_first_device = local_device_to_backend_user(newalice, neworg)

    async with apiv1_backend_sock_factory(backend, neworg.organization_id) as sock:
        # Old token is now invalid
        rep = await organization_bootstrap(
            sock,
            bootstrap_token1,
            backend_newalice.user_certificate,
            backend_newalice_first_device.device_certificate,
            neworg.root_verify_key,
        )
        assert rep == {"status": "not_found"}

        rep = await organization_bootstrap(
            sock,
            bootstrap_token2,
            backend_newalice.user_certificate,
            backend_newalice_first_device.device_certificate,
            neworg.root_verify_key,
        )
        assert rep == {"status": "ok"}

    # Now we are no longer allowed to re-create the organization
    rep = await organization_create(administration_backend_sock, neworg.organization_id)
    assert rep == {"status": "already_exists"}


@pytest.mark.trio
async def test_organization_create_and_bootstrap(
    backend,
    organization_factory,
    local_device_factory,
    alice,
    administration_backend_sock,
    apiv1_backend_sock_factory,
):
    neworg = organization_factory("NewOrg")

    # 1) Create organization, note this means `neworg.bootstrap_token`
    # will contain an invalid token

    rep = await organization_create(administration_backend_sock, neworg.organization_id)
    assert rep == {"status": "ok", "bootstrap_token": ANY}
    bootstrap_token = rep["bootstrap_token"]

    # 2) Bootstrap organization

    # Use an existing user name to make sure they didn't mix together
    newalice = local_device_factory("alice@dev1", neworg, profile=UserProfile.ADMIN)
    backend_newalice, backend_newalice_first_device = local_device_to_backend_user(newalice, neworg)

    async with apiv1_backend_sock_factory(backend, neworg.organization_id) as sock:
        rep = await organization_bootstrap(
            sock,
            bootstrap_token=bootstrap_token,
            user_certificate=backend_newalice.user_certificate,
            device_certificate=backend_newalice_first_device.device_certificate,
            root_verify_key=neworg.root_verify_key,
            redacted_user_certificate=backend_newalice.redacted_user_certificate,
            redacted_device_certificate=backend_newalice_first_device.redacted_device_certificate,
        )
    assert rep == {"status": "ok"}

    # 3) Now our new device can connect the backend

    async with apiv1_backend_sock_factory(backend, newalice) as sock:
        await ping(sock)

    # 4) Make sure alice from the other organization is still working

    async with apiv1_backend_sock_factory(backend, alice) as sock:
        await ping(sock)

    # 5) Finally, check the resulting data in the backend
    backend_user, backend_device = await backend.user.get_user_with_device(
        newalice.organization_id, newalice.device_id
    )
    assert backend_user == backend_newalice
    assert backend_device == backend_newalice_first_device


@pytest.mark.trio
async def test_organization_with_expiration_date_create_and_bootstrap(
    backend,
    organization_factory,
    local_device_factory,
    alice,
    administration_backend_sock,
    apiv1_backend_sock_factory,
):
    neworg = organization_factory("NewOrg")

    # 1) Create organization, note this means `neworg.bootstrap_token`
    # will contain an invalid token

    with freeze_time("2000-01-01"):

        expiration_date = pendulum.Pendulum(2000, 1, 2)
        rep = await organization_create(
            administration_backend_sock, neworg.organization_id, expiration_date=expiration_date
        )
        assert rep == {"status": "ok", "bootstrap_token": ANY, "expiration_date": expiration_date}
        bootstrap_token = rep["bootstrap_token"]

        # 2) Bootstrap organization

        # Use an existing user name to make sure they didn't mix together
        newalice = local_device_factory("alice@dev1", neworg, profile=UserProfile.ADMIN)
        backend_newalice, backend_newalice_first_device = local_device_to_backend_user(
            newalice, neworg
        )

        async with apiv1_backend_sock_factory(backend, neworg.organization_id) as sock:
            rep = await organization_bootstrap(
                sock,
                bootstrap_token,
                backend_newalice.user_certificate,
                backend_newalice_first_device.device_certificate,
                neworg.root_verify_key,
            )
        assert rep == {"status": "ok"}

        # 3) Now our new device can connect the backend

        async with apiv1_backend_sock_factory(backend, newalice) as sock:
            await ping(sock)

        # 4) Make sure alice from the other organization is still working

        async with apiv1_backend_sock_factory(backend, alice) as sock:
            await ping(sock)

    # 5) Now advance after the expiration
    with freeze_time("2000-01-02"):
        # Both anonymous and authenticated connections are refused
        with pytest.raises(HandshakeOrganizationExpired):
            async with apiv1_backend_sock_factory(backend, newalice):
                pass
        with pytest.raises(HandshakeOrganizationExpired):
            async with apiv1_backend_sock_factory(backend, neworg.organization_id):
                pass


@pytest.mark.trio
async def test_organization_expired_create_and_bootstrap(
    backend,
    organization_factory,
    local_device_factory,
    alice,
    administration_backend_sock,
    apiv1_backend_sock_factory,
):
    neworg = organization_factory("NewOrg")

    # 1) Create organization, note this means `neworg.bootstrap_token`
    # will contain an invalid token

    expiration_date = pendulum.now().subtract(days=1)

    rep = await organization_create(
        administration_backend_sock, neworg.organization_id, expiration_date=expiration_date
    )
    assert rep == {"status": "ok", "bootstrap_token": ANY, "expiration_date": expiration_date}

    # 2) Connection to backend for bootstrap purpose is not possible

    with pytest.raises(HandshakeOrganizationExpired):
        async with apiv1_backend_sock_factory(backend, neworg.organization_id):
            pass

    # 3) Now re-create the organization to overwrite the expiration date

    rep = await organization_create(administration_backend_sock, neworg.organization_id)
    assert rep == {"status": "ok", "bootstrap_token": ANY}

    # 4) This time, bootstrap is possible

    async with apiv1_backend_sock_factory(backend, neworg.organization_id):
        pass


@pytest.mark.trio
async def test_organization_bootstrap_bad_data(
    backend_data_binder,
    apiv1_backend_sock_factory,
    organization_factory,
    local_device_factory,
    backend,
    coolorg,
):
    neworg = organization_factory("NewOrg")
    newalice = local_device_factory("alice@dev1", neworg)
    await backend_data_binder.bind_organization(neworg)

    bad_organization_id = coolorg.organization_id
    good_organization_id = neworg.organization_id

    root_signing_key = neworg.root_signing_key
    bad_root_signing_key = coolorg.root_signing_key

    good_bootstrap_token = neworg.bootstrap_token
    bad_bootstrap_token = coolorg.bootstrap_token

    good_rvk = neworg.root_verify_key
    bad_rvk = coolorg.root_verify_key

    good_device_id = newalice.device_id

    good_user_id = newalice.user_id
    bad_user_id = UserID("dummy")

    public_key = newalice.public_key
    verify_key = newalice.verify_key

    now = pendulum.now()
    bad_now = now - pendulum.interval(seconds=1)

    good_cu = UserCertificateContent(
        author=None,
        timestamp=now,
        user_id=good_user_id,
        public_key=public_key,
        profile=UserProfile.ADMIN,
        human_handle=newalice.human_handle,
    )
    good_redacted_cu = good_cu.evolve(human_handle=None)
    good_cd = DeviceCertificateContent(
        author=None,
        timestamp=now,
        device_id=good_device_id,
        device_label=newalice.device_label,
        verify_key=verify_key,
    )
    good_redacted_cd = good_cd.evolve(device_label=None)
    bad_now_cu = good_cu.evolve(timestamp=bad_now)
    bad_now_cd = good_cd.evolve(timestamp=bad_now)
    bad_now_redacted_cu = good_redacted_cu.evolve(timestamp=bad_now)
    bad_now_redacted_cd = good_redacted_cd.evolve(timestamp=bad_now)
    bad_id_cu = good_cu.evolve(user_id=bad_user_id)
    bad_not_admin_cu = good_cu.evolve(profile=UserProfile.STANDARD)

    bad_key_cu = good_cu.dump_and_sign(bad_root_signing_key)
    bad_key_cd = good_cd.dump_and_sign(bad_root_signing_key)

    good_cu = good_cu.dump_and_sign(root_signing_key)
    good_redacted_cu = good_redacted_cu.dump_and_sign(root_signing_key)
    good_cd = good_cd.dump_and_sign(root_signing_key)
    good_redacted_cd = good_redacted_cd.dump_and_sign(root_signing_key)
    bad_now_cu = bad_now_cu.dump_and_sign(root_signing_key)
    bad_now_cd = bad_now_cd.dump_and_sign(root_signing_key)
    bad_now_redacted_cu = bad_now_redacted_cu.dump_and_sign(root_signing_key)
    bad_now_redacted_cd = bad_now_redacted_cd.dump_and_sign(root_signing_key)
    bad_id_cu = bad_id_cu.dump_and_sign(root_signing_key)
    bad_not_admin_cu = bad_not_admin_cu.dump_and_sign(root_signing_key)

    for i, (status, organization_id, *params) in enumerate(
        [
            ("not_found", good_organization_id, bad_bootstrap_token, good_cu, good_cd, good_rvk),
            (
                "already_bootstrapped",
                bad_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
            ),
            (
                "invalid_certification",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                bad_rvk,
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                bad_now_cu,
                good_cd,
                good_rvk,
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                bad_id_cu,
                good_cd,
                good_rvk,
            ),
            (
                "invalid_certification",
                good_organization_id,
                good_bootstrap_token,
                bad_key_cu,
                good_cd,
                good_rvk,
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                bad_now_cd,
                good_rvk,
            ),
            (
                "invalid_certification",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                bad_key_cd,
                good_rvk,
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                bad_not_admin_cu,
                good_cd,
                good_rvk,
            ),
            # Tests with redacted certificates
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                good_cu,  # Not redacted !
                good_redacted_cd,
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                good_redacted_cu,
                good_cd,  # Not redacted !
            ),
            (
                "bad_message",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                None,  # None not allowed
                good_redacted_cd,
            ),
            (
                "bad_message",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                good_redacted_cu,
                None,  # None not allowed
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                bad_now_redacted_cu,
                good_redacted_cd,
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                good_redacted_cu,
                bad_now_redacted_cd,
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                good_redacted_cu,
                _missing,  # Must proved redacted_device if redacted user is present
            ),
            (
                "invalid_data",
                good_organization_id,
                good_bootstrap_token,
                good_cu,
                good_cd,
                good_rvk,
                _missing,  # Must proved redacted_device if redacted user is present
                good_redacted_cd,
            ),
        ]
    ):
        print(f"sub test {i}")
        async with apiv1_backend_sock_factory(backend, organization_id) as sock:
            rep = await organization_bootstrap(sock, *params)
        assert rep["status"] == status

    # Finally cheap test to make sure our "good" data were really good
    async with apiv1_backend_sock_factory(backend, good_organization_id) as sock:
        rep = await organization_bootstrap(
            sock,
            good_bootstrap_token,
            good_cu,
            good_cd,
            good_rvk,
            good_redacted_cu,
            good_redacted_cd,
        )
    assert rep["status"] == "ok"
