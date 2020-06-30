# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from async_generator import asynccontextmanager
from pendulum import Pendulum

from parsec.api.protocol import (
    DeviceID,
    UserID,
    apiv1_user_claim_serializer,
    apiv1_user_get_invitation_creator_serializer,
)
from parsec.backend.backend_events import BackendEvent
from parsec.backend.user import PEER_EVENT_MAX_WAIT, Device, User, UserInvitation
from parsec.event_bus import MetaEvent
from tests.common import freeze_time


@pytest.fixture
async def mallory_invitation(backend, alice, mallory):
    invitation = UserInvitation(mallory.user_id, alice.device_id, Pendulum(2000, 1, 2))
    await backend.user.create_user_invitation(alice.organization_id, invitation)
    return invitation


async def user_get_invitation_creator(sock, **kwargs):
    await sock.send(
        apiv1_user_get_invitation_creator_serializer.req_dumps(
            {"cmd": "user_get_invitation_creator", **kwargs}
        )
    )
    raw_rep = await sock.recv()
    rep = apiv1_user_get_invitation_creator_serializer.rep_loads(raw_rep)
    return rep


@asynccontextmanager
async def user_claim(sock, **kwargs):
    reps = []
    await sock.send(apiv1_user_claim_serializer.req_dumps({"cmd": "user_claim", **kwargs}))
    yield reps
    raw_rep = await sock.recv()
    rep = apiv1_user_claim_serializer.rep_loads(raw_rep)
    reps.append(rep)


@pytest.mark.trio
async def test_user_claim_ok(
    monkeypatch, backend, apiv1_anonymous_backend_sock, coolorg, alice, mallory_invitation
):
    user_invitation_retrieved = trio.Event()

    vanilla_claim_user_invitation = backend.user.claim_user_invitation

    async def _mocked_claim_user_invitation(*args, **kwargs):
        ret = await vanilla_claim_user_invitation(*args, **kwargs)
        user_invitation_retrieved.set()
        return ret

    monkeypatch.setattr(backend.user, "claim_user_invitation", _mocked_claim_user_invitation)

    with freeze_time(mallory_invitation.created_on):
        async with user_claim(
            apiv1_anonymous_backend_sock,
            invited_user_id=mallory_invitation.user_id,
            encrypted_claim=b"<foo>",
        ) as prep:

            # `backend.user.create_user` will destroy the user invitation,
            # so make sure we retrieved it before
            await user_invitation_retrieved.wait()

            # No the user we are waiting for
            await backend.user.create_user(
                alice.organization_id,
                User(
                    user_id=UserID("dummy"),
                    human_handle=None,
                    user_certificate=b"<user certif>",
                    redacted_user_certificate=b"<redacted user certif>",
                    user_certifier=alice.device_id,
                ),
                Device(
                    device_id=DeviceID("dummy@pc1"),
                    device_label=None,
                    device_certificate=b"<device certif>",
                    redacted_device_certificate=b"<redacted device certif>",
                    device_certifier=alice.device_id,
                ),
            )

            await backend.user.create_user(
                alice.organization_id,
                User(
                    user_id=mallory_invitation.user_id,
                    human_handle=None,
                    user_certificate=b"<user certif>",
                    redacted_user_certificate=b"<redacted user certif>",
                    user_certifier=alice.device_id,
                ),
                Device(
                    device_id=mallory_invitation.user_id.to_device_id("pc1"),
                    device_label=None,
                    device_certificate=b"<device certif>",
                    redacted_device_certificate=b"<redacted device certif>",
                    device_certifier=alice.device_id,
                ),
            )

    assert prep[0] == {
        "status": "ok",
        "user_certificate": b"<user certif>",
        "device_certificate": b"<device certif>",
    }


@pytest.mark.trio
async def test_user_claim_timeout(
    mock_clock, backend, apiv1_anonymous_backend_sock, mallory_invitation
):
    with freeze_time(mallory_invitation.created_on), backend.event_bus.listen() as spy:
        async with user_claim(
            apiv1_anonymous_backend_sock,
            invited_user_id=mallory_invitation.user_id,
            encrypted_claim=b"<foo>",
        ) as prep:

            await spy.wait_with_timeout(
                MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_CREATED}
            )
            mock_clock.jump(PEER_EVENT_MAX_WAIT + 1)

    assert prep[0] == {
        "status": "timeout",
        "reason": "Timeout while waiting for invitation creator to answer.",
    }


@pytest.mark.trio
async def test_user_claim_denied(
    backend, apiv1_anonymous_backend_sock, coolorg, mallory_invitation
):
    with freeze_time(mallory_invitation.created_on), backend.event_bus.listen() as spy:
        async with user_claim(
            apiv1_anonymous_backend_sock,
            invited_user_id=mallory_invitation.user_id,
            encrypted_claim=b"<foo>",
        ) as prep:

            await spy.wait_with_timeout(
                MetaEvent.EVENT_CONNECTED, {"event_type": BackendEvent.USER_INVITATION_CANCELLED}
            )
            backend.event_bus.send(
                BackendEvent.USER_CREATED,
                organization_id=coolorg.organization_id,
                user_id="dummy",
                user_certificate=b"<dummy user certif>",
                first_device_id="dummy@dummy",
                first_device_certificate=b"<dummy device certif>",
            )
            backend.event_bus.send(
                BackendEvent.USER_INVITATION_CANCELLED,
                organization_id=coolorg.organization_id,
                user_id=mallory_invitation.user_id,
            )

    assert prep[0] == {"status": "denied", "reason": "Invitation creator rejected us."}


@pytest.mark.trio
async def test_user_claim_unknown(apiv1_anonymous_backend_sock, mallory):
    async with user_claim(
        apiv1_anonymous_backend_sock, invited_user_id=mallory.user_id, encrypted_claim=b"<foo>"
    ) as prep:

        pass

    assert prep[0] == {"status": "not_found"}


@pytest.mark.trio
async def test_user_claim_already_exists(
    mock_clock, backend, apiv1_anonymous_backend_sock, alice, mallory_invitation
):
    await backend.user.create_user(
        alice.organization_id,
        User(
            user_id=mallory_invitation.user_id,
            human_handle=None,
            user_certificate=b"<foo>",
            redacted_user_certificate=b"<foo>",
            user_certifier=alice.device_id,
        ),
        Device(
            device_id=mallory_invitation.user_id.to_device_id("pc1"),
            device_label=None,
            device_certificate=b"<bar>",
            redacted_device_certificate=b"<bar>",
            device_certifier=alice.device_id,
        ),
    )

    with freeze_time(mallory_invitation.created_on):
        async with user_claim(
            apiv1_anonymous_backend_sock,
            invited_user_id=mallory_invitation.user_id,
            encrypted_claim=b"<foo>",
        ) as prep:

            pass

    assert prep[0] == {"status": "not_found"}
