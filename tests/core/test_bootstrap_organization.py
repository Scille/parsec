# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.api.data import UserProfile
from parsec.api.protocol import HumanHandle, OrganizationID
from parsec.core.backend_connection import apiv1_backend_anonymous_cmds_factory
from parsec.core.invite import InviteAlreadyUsedError, InviteNotFoundError, bootstrap_organization
from parsec.core.types import BackendOrganizationBootstrapAddr


@pytest.mark.trio
@pytest.mark.parametrize("with_labels", [False, True])
async def test_good(
    running_backend, backend, alice, bob, alice_backend_cmds, user_fs_factory, with_labels
):
    org_id = OrganizationID("NewOrg")
    org_token = "123456"
    await backend.organization.create(org_id, org_token)

    organization_addr = BackendOrganizationBootstrapAddr.build(
        running_backend.addr, org_id, org_token
    )

    if with_labels:
        human_handle = HumanHandle(email="zack@example.com", label="Zack")
        device_label = "PC1"
    else:
        human_handle = None
        device_label = None

    async with apiv1_backend_anonymous_cmds_factory(addr=organization_addr) as cmds:
        new_device = await bootstrap_organization(
            cmds, human_handle=human_handle, device_label=device_label
        )

    assert new_device is not None
    assert new_device.organization_id == org_id
    assert new_device.device_label == device_label
    assert new_device.human_handle == human_handle
    assert new_device.profile == UserProfile.ADMIN

    # Test the behavior of this new device
    async with user_fs_factory(new_device, initialize_in_v0=True) as newfs:
        await newfs.workspace_create("wa")
        await newfs.sync()

    # Test the device in correct in the backend
    backend_user, backend_device = await backend.user.get_user_with_device(
        org_id, new_device.device_id
    )
    assert backend_user.user_id == new_device.user_id
    assert backend_user.human_handle == new_device.human_handle
    assert backend_user.profile == new_device.profile
    assert backend_user.user_certifier is None
    if with_labels:
        assert backend_user.user_certificate != backend_user.redacted_user_certificate
    else:
        assert backend_user.user_certificate == backend_user.redacted_user_certificate

    assert backend_device.device_id == new_device.device_id
    assert backend_device.device_label == new_device.device_label
    assert backend_device.device_certifier is None
    if with_labels:
        assert backend_device.device_certificate != backend_device.redacted_device_certificate
    else:
        assert backend_device.device_certificate == backend_device.redacted_device_certificate


@pytest.mark.trio
async def test_invalid_token(running_backend, backend):
    org_id = OrganizationID("NewOrg")
    old_token = "123456"
    new_token = "abcdef"
    await backend.organization.create(org_id, old_token)
    await backend.organization.create(org_id, new_token)

    organization_addr = BackendOrganizationBootstrapAddr.build(
        running_backend.addr, org_id, old_token
    )

    async with apiv1_backend_anonymous_cmds_factory(addr=organization_addr) as cmds:
        with pytest.raises(InviteNotFoundError):
            await bootstrap_organization(cmds, human_handle=None, device_label=None)


@pytest.mark.trio
async def test_already_bootstrapped(
    running_backend, backend, alice, bob, alice_backend_cmds, user_fs_factory
):
    org_id = OrganizationID("NewOrg")
    org_token = "123456"
    await backend.organization.create(org_id, org_token)

    organization_addr = BackendOrganizationBootstrapAddr.build(
        running_backend.addr, org_id, org_token
    )

    async with apiv1_backend_anonymous_cmds_factory(addr=organization_addr) as cmds:
        await bootstrap_organization(cmds, human_handle=None, device_label=None)

        with pytest.raises(InviteAlreadyUsedError):
            await bootstrap_organization(cmds, human_handle=None, device_label=None)
