# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.api.protocol import (
    APIV1_ADMINISTRATION_CMDS,
    APIV1_ANONYMOUS_CMDS,
    APIV1_AUTHENTICATED_CMDS,
    AUTHENTICATED_CMDS,
    INVITED_CMDS,
    InvitationType,
    packb,
    unpackb,
)


async def check_forbidden_cmds(backend_sock, cmds):
    for cmd in cmds:
        if cmd == "events_listen":
            # Must pass wait option otherwise backend will hang forever
            await backend_sock.send(packb({"cmd": cmd, "wait": False}))
        else:
            await backend_sock.send(packb({"cmd": cmd}))
        rep = await backend_sock.recv()
        assert unpackb(rep) == {"status": "unknown_command", "reason": "Unknown command"}


async def check_allowed_cmds(backend_sock, cmds):
    for cmd in cmds:
        if cmd == "events_listen":
            # Must pass wait option otherwise backend will hang forever
            await backend_sock.send(packb({"cmd": cmd, "wait": False}))
        else:
            await backend_sock.send(packb({"cmd": cmd}))
        rep = await backend_sock.recv()
        assert unpackb(rep)["status"] != "unknown_command"


@pytest.mark.trio
async def test_invited_has_limited_access(backend, backend_invited_sock_factory, alice):
    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id, greeter_user_id=alice.user_id
    )
    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
    ) as sock:
        await check_forbidden_cmds(sock, AUTHENTICATED_CMDS - INVITED_CMDS)
        await check_allowed_cmds(sock, INVITED_CMDS)


@pytest.mark.trio
async def test_authenticated_has_limited_access(alice_backend_sock):
    await check_forbidden_cmds(alice_backend_sock, INVITED_CMDS - AUTHENTICATED_CMDS)
    await check_allowed_cmds(alice_backend_sock, AUTHENTICATED_CMDS)


@pytest.mark.trio
async def test_apiv1_administration_has_limited_access(administration_backend_sock):
    await check_forbidden_cmds(
        administration_backend_sock,
        (APIV1_ANONYMOUS_CMDS | APIV1_AUTHENTICATED_CMDS) - APIV1_ADMINISTRATION_CMDS,
    )
    await check_allowed_cmds(administration_backend_sock, APIV1_ADMINISTRATION_CMDS)


@pytest.mark.trio
async def test_apiv1_anonymous_has_limited_access(apiv1_anonymous_backend_sock):
    await check_forbidden_cmds(
        apiv1_anonymous_backend_sock,
        (APIV1_ADMINISTRATION_CMDS | APIV1_AUTHENTICATED_CMDS) - APIV1_ANONYMOUS_CMDS,
    )
    await check_allowed_cmds(apiv1_anonymous_backend_sock, APIV1_ANONYMOUS_CMDS)


@pytest.mark.trio
async def test_apiv1_authenticated_has_limited_access(apiv1_alice_backend_sock):
    await check_forbidden_cmds(
        apiv1_alice_backend_sock,
        (APIV1_ADMINISTRATION_CMDS | APIV1_ANONYMOUS_CMDS) - APIV1_AUTHENTICATED_CMDS,
    )
    await check_allowed_cmds(apiv1_alice_backend_sock, APIV1_AUTHENTICATED_CMDS)
