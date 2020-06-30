# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from async_generator import asynccontextmanager

from parsec.api.protocol import UserID, apiv1_user_invite_serializer
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import PEER_EVENT_MAX_WAIT, UserInvitation
from parsec.event_bus import MetaEvent


@asynccontextmanager
async def user_invite(sock, **kwargs):
    reps = []
    await sock.send(apiv1_user_invite_serializer.req_dumps({"cmd": "user_invite", **kwargs}))
    yield reps
    raw_rep = await sock.recv()
    rep = apiv1_user_invite_serializer.rep_loads(raw_rep)
    reps.append(rep)


@pytest.mark.trio
async def test_user_invite(monkeypatch, backend, apiv1_alice_backend_sock, alice, mallory):
    dummy_user_id = UserID("dummy")
    await backend.user.create_user_invitation(
        alice.organization_id, UserInvitation(dummy_user_id, alice.device_id)
    )

    user_invitation_created = trio.Event()

    vanilla_create_user_invitation = backend.user.create_user_invitation

    async def _mocked_create_user_invitation(*args, **kwargs):
        ret = await vanilla_create_user_invitation(*args, **kwargs)
        user_invitation_created.set()
        return ret

    monkeypatch.setattr(backend.user, "create_user_invitation", _mocked_create_user_invitation)

    with trio.fail_after(1):
        async with user_invite(apiv1_alice_backend_sock, user_id=mallory.user_id) as prep:

            # Wait for invitation to be created before fetching it !
            await user_invitation_created.wait()

            # No the user we are waiting for
            await backend.user.claim_user_invitation(
                alice.organization_id, dummy_user_id, b"<dummy encrypted_claim>"
            )

            await backend.user.claim_user_invitation(
                alice.organization_id, mallory.user_id, b"<mallory encrypted_claim>"
            )

    assert prep[0] == {"status": "ok", "encrypted_claim": b"<mallory encrypted_claim>"}


@pytest.mark.trio
async def test_user_invite_already_exists(backend, apiv1_alice_backend_sock, alice, bob):
    with trio.fail_after(1):
        async with user_invite(apiv1_alice_backend_sock, user_id=bob.user_id) as prep:
            pass
    assert prep[0] == {"status": "already_exists", "reason": f"User `{bob.user_id}` already exists"}


@pytest.mark.trio
async def test_user_invite_timeout(mock_clock, backend, apiv1_alice_backend_sock, alice, mallory):
    with backend.event_bus.listen() as spy:
        async with user_invite(apiv1_alice_backend_sock, user_id=mallory.user_id) as prep:

            await spy.wait_with_timeout(
                MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_CLAIMED}
            )
            mock_clock.jump(PEER_EVENT_MAX_WAIT + 1)

    assert prep[0] == {
        "status": "timeout",
        "reason": "Timeout while waiting for new user to be claimed.",
    }


@pytest.mark.trio
async def test_user_invite_not_admin(apiv1_bob_backend_sock, mallory):
    with trio.fail_after(1):
        async with user_invite(apiv1_bob_backend_sock, user_id=mallory.user_id) as prep:
            pass
    assert prep[0] == {
        "status": "not_allowed",
        "reason": "Only allowed for user with ADMIN profile.",
    }


@pytest.mark.trio
async def test_concurrent_user_invite(
    backend, apiv1_alice_backend_sock, apiv1_adam_backend_sock, alice, adam, mallory
):
    with backend.event_bus.listen() as spy, trio.fail_after(1):
        async with user_invite(apiv1_alice_backend_sock, user_id=mallory.user_id) as prep1:

            await spy.wait(MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_CLAIMED})
            async with user_invite(apiv1_adam_backend_sock, user_id=mallory.user_id) as prep2:

                spy.clear()
                await spy.wait(MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_CLAIMED})

                backend.event_bus.send(
                    BackendEvent.USER_CLAIMED,
                    organization_id=mallory.organization_id,
                    user_id=mallory.user_id,
                    encrypted_claim=b"<good>",
                )

    assert prep1[0] == {"status": "ok", "encrypted_claim": b"<good>"}
    assert prep2[0] == {"status": "ok", "encrypted_claim": b"<good>"}


@pytest.mark.trio
async def test_user_invite_same_name_different_organizations(
    backend, apiv1_alice_backend_sock, apiv1_otheralice_backend_sock, alice, otheralice, mallory
):
    # Mallory invitation from first organization
    with backend.event_bus.listen() as spy, trio.fail_after(1):
        async with user_invite(apiv1_alice_backend_sock, user_id=mallory.user_id) as prep:

            # Waiting for user.claimed event
            await spy.wait(MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_CLAIMED})

            backend.event_bus.send(
                BackendEvent.USER_CLAIMED,
                organization_id=alice.organization_id,
                user_id="foo",
                encrypted_claim=b"<dummy>",
            )
            backend.event_bus.send(
                BackendEvent.USER_CLAIMED,
                organization_id=alice.organization_id,
                user_id=mallory.user_id,
                encrypted_claim=b"<good>",
            )

    assert prep[0] == {"status": "ok", "encrypted_claim": b"<good>"}

    # Mallory invitation from second organization
    with backend.event_bus.listen() as spy, trio.fail_after(1):
        async with user_invite(apiv1_otheralice_backend_sock, user_id=mallory.user_id) as prep:

            # Waiting for user.claimed event
            await spy.wait(MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_CLAIMED})

            backend.event_bus.send(
                BackendEvent.USER_CLAIMED,
                organization_id=otheralice.organization_id,
                user_id="foo",
                encrypted_claim=b"<dummy>",
            )
            backend.event_bus.send(
                BackendEvent.USER_CLAIMED,
                organization_id=otheralice.organization_id,
                user_id=mallory.user_id,
                encrypted_claim=b"<good>",
            )

    assert prep[0] == {"status": "ok", "encrypted_claim": b"<good>"}
