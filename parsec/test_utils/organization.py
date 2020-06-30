# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


import os
from pathlib import Path
from typing import Tuple

import trio

from parsec.api.data import UserProfile
from parsec.api.protocol import HumanHandle, InvitationType, OrganizationID
from parsec.core import logged_core_factory
from parsec.core.backend_connection import (
    apiv1_backend_administration_cmds_factory,
    apiv1_backend_anonymous_cmds_factory,
    backend_authenticated_cmds_factory,
    backend_invited_cmds_factory,
)
from parsec.core.config import load_config
from parsec.core.invite import (
    DeviceGreetInitialCtx,
    UserGreetInitialCtx,
    bootstrap_organization,
    claimer_retrieve_info,
)
from parsec.core.local_device import save_device_with_password
from parsec.core.types import (
    BackendAddr,
    BackendInvitationAddr,
    BackendOrganizationBootstrapAddr,
    LocalDevice,
    WorkspaceRole,
)
from parsec.logging import configure_logging


async def initialize_test_organization(
    config_dir: Path,
    backend_address: BackendAddr,
    password: str,
    administration_token: str,
    force: bool,
) -> Tuple[LocalDevice, LocalDevice, LocalDevice]:

    configure_logging("WARNING")

    organization_id = OrganizationID("Org")

    # Create organization

    async with apiv1_backend_administration_cmds_factory(
        backend_address, administration_token
    ) as administration_cmds:

        rep = await administration_cmds.organization_create(organization_id)
        assert rep["status"] == "ok"
        bootstrap_token = rep["bootstrap_token"]

        organization_bootstrap_addr = BackendOrganizationBootstrapAddr.build(
            backend_address, organization_id, bootstrap_token
        )

    # Bootstrap organization and Alice user

    async with apiv1_backend_anonymous_cmds_factory(organization_bootstrap_addr) as anonymous_cmds:
        alice_device = await bootstrap_organization(
            cmds=anonymous_cmds,
            human_handle=HumanHandle(label="Alice", email="alice@example.com"),
            device_label="laptop",
        )
        save_device_with_password(config_dir, alice_device, password, force=force)

    # Create a workspace for Alice

    config = load_config(config_dir, debug="DEBUG" in os.environ)
    async with logged_core_factory(config, alice_device) as core:
        alice_ws_id = await core.user_fs.workspace_create("alice_workspace")
        await core.user_fs.sync()

    # Register a new device for Alice

    other_alice_device = None
    async with backend_authenticated_cmds_factory(
        addr=alice_device.organization_addr,
        device_id=alice_device.device_id,
        signing_key=alice_device.signing_key,
    ) as alice_cmds:
        rep = await alice_cmds.invite_new(type=InvitationType.DEVICE)
        assert rep["status"] == "ok"
        invitation_addr = BackendInvitationAddr.build(
            backend_addr=alice_device.organization_addr,
            organization_id=alice_device.organization_id,
            invitation_type=InvitationType.DEVICE,
            token=rep["token"],
        )

        async with backend_invited_cmds_factory(addr=invitation_addr) as invited_cmds:

            async def invite_task():
                initial_ctx = DeviceGreetInitialCtx(cmds=alice_cmds, token=invitation_addr.token)
                in_progress_ctx = await initial_ctx.do_wait_peer()
                in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()
                in_progress_ctx = await in_progress_ctx.do_signify_trust()
                in_progress_ctx = await in_progress_ctx.do_get_claim_requests()
                await in_progress_ctx.do_create_new_device(
                    author=alice_device, device_label=in_progress_ctx.requested_device_label
                )

            async def claim_task():
                nonlocal other_alice_device
                initial_ctx = await claimer_retrieve_info(cmds=invited_cmds)
                in_progress_ctx = await initial_ctx.do_wait_peer()
                in_progress_ctx = await in_progress_ctx.do_signify_trust()
                in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()
                other_alice_device = await in_progress_ctx.do_claim_device(
                    requested_device_label="pc"
                )

            async with trio.open_service_nursery() as nursery:
                nursery.start_soon(invite_task)
                nursery.start_soon(claim_task)

            save_device_with_password(config_dir, other_alice_device, password, force=force)

        # Invite Bob in

        bob_device = None
        rep = await alice_cmds.invite_new(type=InvitationType.USER, claimer_email="bob@example.com")
        assert rep["status"] == "ok"
        invitation_addr = BackendInvitationAddr.build(
            backend_addr=alice_device.organization_addr,
            organization_id=alice_device.organization_id,
            invitation_type=InvitationType.USER,
            token=rep["token"],
        )

        async with backend_invited_cmds_factory(addr=invitation_addr) as invited_cmds:

            async def invite_task():
                initial_ctx = UserGreetInitialCtx(cmds=alice_cmds, token=invitation_addr.token)
                in_progress_ctx = await initial_ctx.do_wait_peer()
                in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()
                in_progress_ctx = await in_progress_ctx.do_signify_trust()
                in_progress_ctx = await in_progress_ctx.do_get_claim_requests()
                await in_progress_ctx.do_create_new_user(
                    author=alice_device,
                    human_handle=in_progress_ctx.requested_human_handle,
                    device_label=in_progress_ctx.requested_device_label,
                    profile=UserProfile.STANDARD,
                )

            async def claim_task():
                nonlocal bob_device
                initial_ctx = await claimer_retrieve_info(cmds=invited_cmds)
                in_progress_ctx = await initial_ctx.do_wait_peer()
                in_progress_ctx = await in_progress_ctx.do_signify_trust()
                in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()
                bob_device = await in_progress_ctx.do_claim_user(
                    requested_human_handle=HumanHandle(label="Bob", email="bob@example.com"),
                    requested_device_label="laptop",
                )

            async with trio.open_service_nursery() as nursery:
                nursery.start_soon(invite_task)
                nursery.start_soon(claim_task)

            save_device_with_password(config_dir, bob_device, password, force=force)

    # Create bob workspace and share with Alice

    async with logged_core_factory(config, bob_device) as core:
        bob_ws_id = await core.user_fs.workspace_create("bob_workspace")
        await core.user_fs.workspace_share(bob_ws_id, alice_device.user_id, WorkspaceRole.MANAGER)

    # Share Alice workspace with bob

    async with logged_core_factory(config, alice_device) as core:
        await core.user_fs.workspace_share(alice_ws_id, bob_device.user_id, WorkspaceRole.MANAGER)

    # Synchronize every device
    for device in (alice_device, other_alice_device, bob_device):
        async with logged_core_factory(config, device) as core:
            await core.user_fs.process_last_messages()
            await core.user_fs.sync()

    return (alice_device, other_alice_device, bob_device)
