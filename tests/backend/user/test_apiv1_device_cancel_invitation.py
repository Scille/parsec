# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from pendulum import Pendulum

from parsec.api.protocol import (
    apiv1_device_cancel_invitation_serializer,
    apiv1_device_claim_serializer,
)
from parsec.backend.user import DeviceInvitation
from tests.common import freeze_time


@pytest.fixture
async def alice_nd_invitation(backend, alice):
    invitation = DeviceInvitation(
        alice.user_id.to_device_id("new_device"), alice.device_id, Pendulum(2000, 1, 2)
    )
    await backend.user.create_device_invitation(alice.organization_id, invitation)
    return invitation


async def device_cancel_invitation(sock, **kwargs):
    await sock.send(
        apiv1_device_cancel_invitation_serializer.req_dumps(
            {"cmd": "device_cancel_invitation", **kwargs}
        )
    )
    raw_rep = await sock.recv()
    rep = apiv1_device_cancel_invitation_serializer.rep_loads(raw_rep)
    return rep


async def device_claim_cancelled_invitation(sock, **kwargs):
    await sock.send(apiv1_device_claim_serializer.req_dumps({"cmd": "device_claim", **kwargs}))
    with trio.fail_after(1):
        raw_rep = await sock.recv()
    return apiv1_device_claim_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_device_cancel_invitation_ok(
    apiv1_alice_backend_sock, alice_nd_invitation, apiv1_anonymous_backend_sock
):
    rep = await device_cancel_invitation(
        apiv1_alice_backend_sock, invited_device_name=alice_nd_invitation.device_id.device_name
    )
    assert rep == {"status": "ok"}

    # Try to use the cancelled invitation
    with freeze_time(alice_nd_invitation.created_on):
        rep = await device_claim_cancelled_invitation(
            apiv1_anonymous_backend_sock,
            invited_device_id=alice_nd_invitation.device_id,
            encrypted_claim=b"<foo>",
        )
        assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_device_cancel_invitation_unknown(apiv1_alice_backend_sock, alice):
    rep = await device_cancel_invitation(apiv1_alice_backend_sock, invited_device_name="foo")
    assert rep == {"status": "ok"}


@pytest.mark.trio
async def test_device_cancel_invitation_other_organization(
    sock_from_other_organization_factory, alice, backend, alice_nd_invitation
):
    # Organizations should be isolated
    async with sock_from_other_organization_factory(backend, mimick=alice.device_id) as sock:
        rep = await device_cancel_invitation(
            sock, invited_device_name=alice_nd_invitation.device_id.device_name
        )
        # Cancel returns even if no invitation was found
        assert rep == {"status": "ok"}

    # Make sure the invitation still works
    invitation = await backend.user.get_device_invitation(
        alice.organization_id, alice_nd_invitation.device_id
    )
    assert invitation == alice_nd_invitation
