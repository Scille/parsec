# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import click

from parsec.core.cli import (
    apiv1_bootstrap_organization,
    apiv1_claim_device,
    apiv1_claim_user,
    apiv1_invite_device,
    apiv1_invite_user,
    bootstrap_organization,
    create_organization,
    create_workspace,
    invitation,
    list_devices,
    run,
    share_workspace,
    stats_organization,
    status_organization,
)

__all__ = ("core_cmd",)


@click.group()
def core_cmd():
    pass


core_cmd.add_command(run.run_gui, "gui")
core_cmd.add_command(run.run_mountpoint, "run")
core_cmd.add_command(create_workspace.create_workspace, "create_workspace")
core_cmd.add_command(share_workspace.share_workspace, "share_workspace")
core_cmd.add_command(list_devices.list_devices, "list_devices")

core_cmd.add_command(invitation.invite_user, "invite_user")
core_cmd.add_command(invitation.invite_device, "invite_device")
core_cmd.add_command(invitation.list_invitations, "list_invitations")
core_cmd.add_command(invitation.greet_invitation, "greet_invitation")
core_cmd.add_command(invitation.claim_invitation, "claim_invitation")
core_cmd.add_command(invitation.cancel_invitation, "cancel_invitation")


@click.group()
def apiv1_cmd():
    pass


apiv1_cmd.add_command(apiv1_invite_user.invite_user, "invite_user")
apiv1_cmd.add_command(apiv1_claim_user.claim_user, "claim_user")
apiv1_cmd.add_command(apiv1_invite_device.invite_device, "invite_device")
apiv1_cmd.add_command(apiv1_claim_device.claim_device, "claim_device")
apiv1_cmd.add_command(apiv1_bootstrap_organization.bootstrap_organization, "bootstrap_organization")
core_cmd.add_command(apiv1_cmd, "apiv1")

core_cmd.add_command(create_organization.create_organization, "create_organization")
core_cmd.add_command(stats_organization.stats_organization, "stats_organization")
core_cmd.add_command(status_organization.status_organization, "status_organization")
core_cmd.add_command(bootstrap_organization.bootstrap_organization, "bootstrap_organization")
