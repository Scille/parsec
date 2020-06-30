# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from functools import partial
from uuid import uuid4

import pytest
import trio
from pendulum import Pendulum

from parsec.api.protocol import InvitationDeletedReason, InvitationType
from parsec.api.transport import TransportError
from parsec.backend.backend_events import BackendEvent
from parsec.crypto import PrivateKey
from tests.backend.common import (
    invite_1_claimer_wait_peer,
    invite_1_greeter_wait_peer,
    invite_2a_claimer_send_hashed_nonce,
    invite_2a_greeter_get_hashed_nonce,
    invite_2b_claimer_send_nonce,
    invite_2b_greeter_send_nonce,
    invite_3a_claimer_signify_trust,
    invite_3a_greeter_wait_peer_trust,
    invite_3b_claimer_wait_peer_trust,
    invite_3b_greeter_signify_trust,
    invite_4_claimer_communicate,
    invite_4_greeter_communicate,
    ping,
)


@pytest.mark.trio
@pytest.mark.parametrize("type", ("deleted_invitation", "unknown_token"))
async def test_greeter_exchange_bad_access(alice, backend, alice_backend_sock, type):
    if type == "deleted_invitation":
        invitation = await backend.invite.new_for_device(
            organization_id=alice.organization_id, greeter_user_id=alice.user_id
        )
        await backend.invite.delete(
            organization_id=alice.organization_id,
            greeter=alice.user_id,
            token=invitation.token,
            on=Pendulum(2000, 1, 2),
            reason=InvitationDeletedReason.ROTTEN,
        )
        token = invitation.token
        status = "already_deleted"
    else:
        token = uuid4()
        status = "not_found"

    greeter_privkey = PrivateKey.generate()
    with trio.fail_after(1):
        rep = await invite_1_greeter_wait_peer(
            alice_backend_sock, token=token, greeter_public_key=greeter_privkey.public_key
        )
    assert rep == {"status": status}

    with trio.fail_after(1):
        rep = await invite_2a_greeter_get_hashed_nonce(alice_backend_sock, token=token)
    assert rep == {"status": status}

    with trio.fail_after(1):
        rep = await invite_2b_greeter_send_nonce(
            alice_backend_sock, token=token, greeter_nonce=b"<greeter_nonce>"
        )
    assert rep == {"status": status}

    with trio.fail_after(1):
        rep = await invite_3a_greeter_wait_peer_trust(alice_backend_sock, token=token)
    assert rep == {"status": status}

    with trio.fail_after(1):
        rep = await invite_3b_greeter_signify_trust(alice_backend_sock, token=token)
    assert rep == {"status": status}

    with trio.fail_after(1):
        rep = await invite_4_greeter_communicate(
            alice_backend_sock, token=token, payload=b"<payload>"
        )
    assert rep == {"status": status}


@pytest.mark.trio
async def test_invited_connection_closed_on_invitation_deletion(
    alice, backend, backend_invited_sock_factory
):
    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id, greeter_user_id=alice.user_id
    )

    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
        freeze_on_transport_error=False,
    ) as invited_sock:

        await backend.invite.delete(
            organization_id=alice.organization_id,
            greeter=alice.user_id,
            token=invitation.token,
            on=Pendulum(2000, 1, 2),
            reason=InvitationDeletedReason.ROTTEN,
        )
        with pytest.raises(TransportError):
            with trio.fail_after(1):
                # The event triggering the closing of the connection may
                # take some time to kick in
                while True:
                    await ping(invited_sock)


@pytest.mark.trio
@pytest.mark.parametrize(
    "action",
    (
        "1_wait_peer",
        "2a_send_hashed_nonce",
        "2b_send_nonce",
        "3a_signify_trust",
        "3b_wait_peer_trust",
        "4_communicate",
    ),
)
async def test_claimer_exchange_bad_access(alice, backend, backend_invited_sock_factory, action):
    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id, greeter_user_id=alice.user_id
    )

    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
        freeze_on_transport_error=False,
    ) as invited_sock:

        if action == "1_wait_peer":
            claimer_privkey = PrivateKey.generate()
            call = partial(
                invite_1_claimer_wait_peer,
                invited_sock,
                claimer_public_key=claimer_privkey.public_key,
            )
        elif action == "2a_send_hashed_nonce":
            call = partial(
                invite_2a_claimer_send_hashed_nonce,
                invited_sock,
                claimer_hashed_nonce=b"<claimer_hashed_nonce>",
            )
        elif action == "2b_send_nonce":
            call = partial(
                invite_2b_claimer_send_nonce, invited_sock, claimer_nonce=b"<claimer_nonce>"
            )
        elif action == "3a_signify_trust":
            call = partial(invite_3a_claimer_signify_trust, invited_sock)
        elif action == "3b_wait_peer_trust":
            call = partial(invite_3b_claimer_wait_peer_trust, invited_sock)
        elif action == "4_communicate":
            call = partial(invite_4_claimer_communicate, invited_sock, payload=b"<payload>")

        # Disable the callback responsible for closing the claimer's connection
        # on invitation deletion. This way we can test connection behavior
        # when the automatic closing takes time to be processed.
        backend.event_bus.mute(BackendEvent.INVITE_STATUS_CHANGED)

        await backend.invite.delete(
            organization_id=alice.organization_id,
            greeter=alice.user_id,
            token=invitation.token,
            on=Pendulum(2000, 1, 2),
            reason=InvitationDeletedReason.ROTTEN,
        )
        rep = await call()
        assert rep == {"status": "already_deleted"}
