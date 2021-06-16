# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pytest
import trio
from unittest.mock import ANY
from pendulum import datetime

from parsec.backend.backend_events import BackendEvent
from parsec.api.transport import TransportError
from parsec.api.data import UserProfile
from parsec.api.protocol import (
    InvitationStatus,
    InvitationDeletedReason,
    InvitationType,
    HandshakeBadIdentity,
    APIEvent,
)

from tests.common import freeze_time, customize_fixtures
from tests.backend.common import (
    invite_new,
    invite_list,
    invite_delete,
    invite_info,
    events_subscribe,
    events_listen_wait,
)


@pytest.mark.trio
async def test_user_create_and_info(
    backend, alice, alice_backend_sock, alice2_backend_sock, backend_invited_sock_factory
):
    # Provide other unrelated invatitons that should stay unchanged
    with backend.event_bus.listen() as spy:
        other_device_invitation = await backend.invite.new_for_device(
            organization_id=alice.organization_id,
            greeter_user_id=alice.user_id,
            created_on=datetime(2000, 1, 2),
        )
        other_user_invitation = await backend.invite.new_for_user(
            organization_id=alice.organization_id,
            greeter_user_id=alice.user_id,
            claimer_email="other@example.com",
            created_on=datetime(2000, 1, 3),
        )
        await spy.wait_multiple_with_timeout(
            [BackendEvent.INVITE_STATUS_CHANGED, BackendEvent.INVITE_STATUS_CHANGED]
        )

    await events_subscribe(alice2_backend_sock)

    with freeze_time("2000-01-04"):
        rep = await invite_new(
            alice_backend_sock, type=InvitationType.USER, claimer_email="zack@example.com"
        )
    assert rep == {"status": "ok", "token": ANY}
    token = rep["token"]

    with trio.fail_after(1):
        rep = await events_listen_wait(alice2_backend_sock)
    assert rep == {
        "status": "ok",
        "event": APIEvent.INVITE_STATUS_CHANGED,
        "invitation_status": InvitationStatus.IDLE,
        "token": token,
    }

    rep = await invite_list(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "invitations": [
            {
                "type": InvitationType.DEVICE,
                "token": other_device_invitation.token,
                "created_on": datetime(2000, 1, 2),
                "status": InvitationStatus.IDLE,
            },
            {
                "type": InvitationType.USER,
                "token": other_user_invitation.token,
                "created_on": datetime(2000, 1, 3),
                "claimer_email": "other@example.com",
                "status": InvitationStatus.IDLE,
            },
            {
                "type": InvitationType.USER,
                "token": token,
                "created_on": datetime(2000, 1, 4),
                "claimer_email": "zack@example.com",
                "status": InvitationStatus.IDLE,
            },
        ],
    }

    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.USER,
        token=token,
    ) as invited_sock:
        rep = await invite_info(invited_sock)
        assert rep == {
            "status": "ok",
            "type": InvitationType.USER,
            "claimer_email": "zack@example.com",
            "greeter_user_id": alice.user_id,
            "greeter_human_handle": alice.human_handle,
        }


@pytest.mark.trio
async def test_device_create_and_info(
    backend, alice, alice_backend_sock, alice2_backend_sock, backend_invited_sock_factory
):

    # Provide other unrelated invitations that should stay unchanged
    with backend.event_bus.listen() as spy:
        other_user_invitation = await backend.invite.new_for_user(
            organization_id=alice.organization_id,
            greeter_user_id=alice.user_id,
            claimer_email="other@example.com",
            created_on=datetime(2000, 1, 2),
        )
        await spy.wait_multiple_with_timeout([BackendEvent.INVITE_STATUS_CHANGED])

    await events_subscribe(alice2_backend_sock)

    with freeze_time("2000-01-03"):
        rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE)
    assert rep == {"status": "ok", "token": ANY}
    token = rep["token"]

    with trio.fail_after(1):
        rep = await events_listen_wait(alice2_backend_sock)
    assert rep == {
        "status": "ok",
        "event": APIEvent.INVITE_STATUS_CHANGED,
        "invitation_status": InvitationStatus.IDLE,
        "token": token,
    }

    rep = await invite_list(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "invitations": [
            {
                "type": InvitationType.USER,
                "token": other_user_invitation.token,
                "created_on": datetime(2000, 1, 2),
                "claimer_email": "other@example.com",
                "status": InvitationStatus.IDLE,
            },
            {
                "type": InvitationType.DEVICE,
                "token": token,
                "created_on": datetime(2000, 1, 3),
                "status": InvitationStatus.IDLE,
            },
        ],
    }

    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=token,
    ) as invited_sock:
        rep = await invite_info(invited_sock)
        assert rep == {
            "status": "ok",
            "type": InvitationType.DEVICE,
            "greeter_user_id": alice.user_id,
            "greeter_human_handle": alice.human_handle,
        }


@pytest.mark.trio
async def test_invite_with_users_limit(backend, alice, alice_backend_sock):
    org = await backend.organization.get(alice.organization_id)
    nb_users = (await backend.user.find(alice.organization_id))[1]

    assert nb_users == 3
    assert org.users_limit is None

    # User invitation
    rep = await invite_new(
        alice_backend_sock,
        type=InvitationType.USER,
        claimer_email="zack@example.com",
        send_email=False,
    )
    assert rep == {"status": "ok", "token": ANY}

    await backend.organization.update(alice.organization_id, users_limit=3)
    org = await backend.organization.get(alice.organization_id)
    assert org.users_limit == 3

    # User invitation
    rep = await invite_new(
        alice_backend_sock,
        type=InvitationType.USER,
        claimer_email="toto@example.com",
        send_email=False,
    )
    assert rep == {"status": "not_allowed", "reason": "User's limit reached"}


@pytest.mark.trio
async def test_invite_with_send_mail(alice, alice_backend_sock, email_letterbox):
    # User invitation
    rep = await invite_new(
        alice_backend_sock,
        type=InvitationType.USER,
        claimer_email="zack@example.com",
        send_email=True,
    )
    assert rep == {"status": "ok", "token": ANY}
    token = rep["token"]
    email = await email_letterbox.get_next_with_timeout()
    assert email == ("zack@example.com", ANY)

    # Lame checks on the sent email
    body = str(email[1])
    assert body.startswith("Content-Type: multipart/alternative;")
    assert 'Content-Type: text/plain; charset="us-ascii"' in body
    assert 'Content-Type: text/html; charset="us-ascii"' in body
    assert "Subject: [Parsec] Alicey McAliceFace invited you to CoolOrg" in body
    assert "From: Parsec <no-reply@parsec.com>" in body
    assert "To: zack@example.com" in body
    assert "Reply-To: Alicey McAliceFace <alice@example.com>" in body
    assert token.hex in body
    assert (
        "You have received an invitation from Alicey McAliceFace to join the CoolOrg organization on Parsec."
        in body
    )

    # Device invitation
    rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE, send_email=True)
    assert rep == {"status": "ok", "token": ANY}
    token = rep["token"]
    email = await email_letterbox.get_next_with_timeout()
    assert email == (alice.human_handle.email, ANY)

    # Lame checks on the sent email
    body = str(email[1])
    assert body.startswith("Content-Type: multipart/alternative;")
    assert 'Content-Type: text/plain; charset="us-ascii"' in body
    assert 'Content-Type: text/html; charset="us-ascii"' in body
    assert "Subject: [Parsec] New device invitation to CoolOrg" in body
    assert "From: Parsec <no-reply@parsec.com>" in body
    assert "To: alice@example.com" in body
    assert "Reply-To: " not in body
    assert token.hex in body
    assert (
        "You have received an invitation to add a new device to the CoolOrg organization on Parsec."
        in body
    )


@pytest.mark.trio
@customize_fixtures(alice_has_human_handle=False)
async def test_invite_with_send_mail_and_greeter_without_human_handle(
    alice, alice_backend_sock, email_letterbox
):
    # User invitation
    rep = await invite_new(
        alice_backend_sock,
        type=InvitationType.USER,
        claimer_email="zack@example.com",
        send_email=True,
    )
    assert rep == {"status": "ok", "token": ANY}
    token = rep["token"]
    email = await email_letterbox.get_next_with_timeout()
    assert email == ("zack@example.com", ANY)

    # Lame checks on the sent email
    body = str(email[1])
    assert body.startswith("Content-Type: multipart/alternative;")
    assert 'Content-Type: text/plain; charset="us-ascii"' in body
    assert 'Content-Type: text/html; charset="us-ascii"' in body
    assert "Subject: [Parsec] alice invited you to CoolOrg" in body
    assert "From: Parsec <no-reply@parsec.com>" in body
    assert "To: zack@example.com" in body
    assert "Reply-To: " not in body
    assert token.hex in body

    # Device invitation (not avaible given no human_handle means no email !)
    rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE, send_email=True)
    assert rep == {"status": "not_available"}


@pytest.mark.trio
@customize_fixtures(alice_profile=UserProfile.OUTSIDER)
async def test_invite_new_limited_for_outsider(alice_backend_sock):
    rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE)
    assert rep == {"status": "ok", "token": ANY}

    # Only ADMIN can invite new users
    rep = await invite_new(
        alice_backend_sock, type=InvitationType.USER, claimer_email="zack@example.com"
    )
    assert rep == {"status": "not_allowed"}


@pytest.mark.trio
@customize_fixtures(alice_profile=UserProfile.STANDARD)
async def test_invite_new_limited_for_standard(alice_backend_sock):
    # Outsider can only invite new devices
    rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE)
    assert rep == {"status": "ok", "token": ANY}

    # Only ADMIN can invite new users
    rep = await invite_new(
        alice_backend_sock, type=InvitationType.USER, claimer_email="zack@example.com"
    )
    assert rep == {"status": "not_allowed"}


@pytest.mark.trio
async def test_delete(
    alice, backend, alice_backend_sock, alice2_backend_sock, backend_invited_sock_factory
):
    with backend.event_bus.listen() as spy:
        invitation = await backend.invite.new_for_device(
            organization_id=alice.organization_id,
            greeter_user_id=alice.user_id,
            created_on=datetime(2000, 1, 2),
        )
        await spy.wait_multiple_with_timeout([BackendEvent.INVITE_STATUS_CHANGED])

    await events_subscribe(alice2_backend_sock)

    with backend.event_bus.listen() as spy:
        with freeze_time("2000-01-03"):
            rep = await invite_delete(
                alice_backend_sock, token=invitation.token, reason=InvitationDeletedReason.CANCELLED
            )
        assert rep == {"status": "ok"}
        await spy.wait_with_timeout(BackendEvent.INVITE_STATUS_CHANGED)

    with trio.fail_after(1):
        rep = await events_listen_wait(alice2_backend_sock)
    assert rep == {
        "status": "ok",
        "event": APIEvent.INVITE_STATUS_CHANGED,
        "invitation_status": InvitationStatus.DELETED,
        "token": invitation.token,
    }

    # Deleted invitation are no longer visible
    rep = await invite_list(alice_backend_sock)
    assert rep == {"status": "ok", "invitations": []}

    # Can no longer use this invitation to connect to the backend
    with pytest.raises(HandshakeBadIdentity):
        async with backend_invited_sock_factory(
            backend,
            organization_id=alice.organization_id,
            invitation_type=InvitationType.DEVICE,
            token=invitation.token,
        ):
            pass


@pytest.mark.trio
@pytest.mark.parametrize("is_revoked", [True, False])
async def test_user_invitation_already_member(
    alice, bob, backend, alice_backend_sock, is_revoked, backend_data_binder
):
    if is_revoked:
        await backend_data_binder.bind_revocation(user_id=bob.user_id, certifier=alice)

    rep = await invite_new(
        alice_backend_sock, type=InvitationType.USER, claimer_email=bob.human_handle.email
    )
    if not is_revoked:
        assert rep == {"status": "already_member"}
    else:
        assert rep == {"status": "ok", "token": ANY}


@pytest.mark.trio
async def test_user_invitation_double_create(alice, backend, alice_backend_sock):
    claimer_email = "zack@example.com"

    invitation = await backend.invite.new_for_user(
        organization_id=alice.organization_id,
        claimer_email=claimer_email,
        greeter_user_id=alice.user_id,
        created_on=datetime(2000, 1, 2),
    )

    # Calling invite_new should be idempotent
    with freeze_time("2000-01-03"):
        rep = await invite_new(
            alice_backend_sock, type=InvitationType.USER, claimer_email=claimer_email
        )
        assert rep == {"status": "ok", "token": invitation.token}

        rep = await invite_new(
            alice_backend_sock, type=InvitationType.USER, claimer_email=claimer_email
        )
        assert rep == {"status": "ok", "token": invitation.token}

    rep = await invite_list(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "invitations": [
            {
                "type": InvitationType.USER,
                "token": invitation.token,
                "created_on": datetime(2000, 1, 2),
                "claimer_email": claimer_email,
                "status": InvitationStatus.IDLE,
            }
        ],
    }


@pytest.mark.trio
async def test_device_invitation_double_create(
    alice, backend, alice_backend_sock, backend_invited_sock_factory
):
    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id,
        greeter_user_id=alice.user_id,
        created_on=datetime(2000, 1, 2),
    )

    # Calling invite_new should be idempotent
    with freeze_time("2000-01-03"):
        rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE)
        assert rep == {"status": "ok", "token": invitation.token}

        rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE)
        assert rep == {"status": "ok", "token": invitation.token}

    rep = await invite_list(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "invitations": [
            {
                "type": InvitationType.DEVICE,
                "token": invitation.token,
                "created_on": datetime(2000, 1, 2),
                "status": InvitationStatus.IDLE,
            }
        ],
    }


@pytest.mark.trio
async def test_user_invitation_recreate_deleted(
    alice, backend, alice_backend_sock, backend_invited_sock_factory
):
    claimer_email = "zack@example.com"
    invitation = await backend.invite.new_for_user(
        organization_id=alice.organization_id,
        claimer_email=claimer_email,
        greeter_user_id=alice.user_id,
        created_on=datetime(2000, 1, 2),
    )
    await backend.invite.delete(
        organization_id=alice.organization_id,
        greeter=invitation.greeter_user_id,
        token=invitation.token,
        on=datetime(2000, 1, 3),
        reason=InvitationDeletedReason.FINISHED,
    )

    # Deleted invitation shoudn't prevent from creating a new one

    with freeze_time("2000-01-04"):
        rep = await invite_new(
            alice_backend_sock, type=InvitationType.USER, claimer_email=claimer_email
        )
    assert rep == {"status": "ok", "token": ANY}
    new_token = rep["token"]
    assert new_token != invitation.token

    rep = await invite_list(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "invitations": [
            {
                "type": InvitationType.USER,
                "token": new_token,
                "created_on": datetime(2000, 1, 4),
                "claimer_email": claimer_email,
                "status": InvitationStatus.IDLE,
            }
        ],
    }


@pytest.mark.trio
async def test_device_invitation_recreate_deleted(
    alice, backend, alice_backend_sock, backend_invited_sock_factory
):
    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id,
        greeter_user_id=alice.user_id,
        created_on=datetime(2000, 1, 2),
    )
    await backend.invite.delete(
        organization_id=alice.organization_id,
        greeter=invitation.greeter_user_id,
        token=invitation.token,
        on=datetime(2000, 1, 3),
        reason=InvitationDeletedReason.FINISHED,
    )

    # Deleted invitation shoudn't prevent from creating a new one

    with freeze_time("2000-01-04"):
        rep = await invite_new(alice_backend_sock, type=InvitationType.DEVICE)
    assert rep == {"status": "ok", "token": ANY}
    new_token = rep["token"]
    assert new_token != invitation.token

    rep = await invite_list(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "invitations": [
            {
                "type": InvitationType.DEVICE,
                "token": new_token,
                "created_on": datetime(2000, 1, 4),
                "status": InvitationStatus.IDLE,
            }
        ],
    }


@pytest.mark.trio
async def test_delete_invitation_while_claimer_connected(
    backend, alice, backend_invited_sock_factory
):
    invitation = await backend.invite.new_for_user(
        organization_id=alice.organization_id,
        greeter_user_id=alice.user_id,
        claimer_email="zack@example.com",
    )
    other_invitation = await backend.invite.new_for_user(
        organization_id=alice.organization_id,
        greeter_user_id=alice.user_id,
        claimer_email="kcaz@example.com",
    )

    # Invitation is valid so handshake is allowed
    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=invitation.TYPE,
        token=invitation.token,
        freeze_on_transport_error=False,
    ) as invited_sock:
        async with backend_invited_sock_factory(
            backend,
            organization_id=alice.organization_id,
            invitation_type=other_invitation.TYPE,
            token=other_invitation.token,
            freeze_on_transport_error=False,
        ) as other_invited_sock:

            # Delete the invitation, claimer connection should be closed automatically
            await backend.invite.delete(
                organization_id=alice.organization_id,
                greeter=alice.user_id,
                token=invitation.token,
                on=datetime(2000, 1, 2),
                reason=InvitationDeletedReason.ROTTEN,
            )

            with pytest.raises(TransportError):
                with trio.fail_after(1):
                    # Claimer connection can take some time to be closed
                    while True:
                        rep = await invite_info(invited_sock)
                        # Invitation info are cached for the connection
                        # at handshake time, hence if the command won't take
                        # into account the fact the invitation has been deleted
                        assert rep["status"] == "ok"

            # However other invitations shouldn't have been affected
            rep = await invite_info(other_invited_sock)
            assert rep["status"] == "ok"


@pytest.mark.trio
async def test_already_deleted(alice, backend, alice_backend_sock, backend_invited_sock_factory):
    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id, greeter_user_id=alice.user_id
    )

    await backend.invite.delete(
        organization_id=alice.organization_id,
        greeter=alice.user_id,
        token=invitation.token,
        on=datetime(2000, 1, 2),
        reason=InvitationDeletedReason.ROTTEN,
    )

    rep = await invite_delete(
        alice_backend_sock, token=invitation.token, reason=InvitationDeletedReason.CANCELLED
    )
    assert rep == {"status": "already_deleted"}


@pytest.mark.trio
async def test_isolated_between_users(
    alice, bob, backend, backend_invited_sock_factory, alice_backend_sock
):
    invitation = await backend.invite.new_for_device(
        organization_id=bob.organization_id, greeter_user_id=bob.user_id
    )

    rep = await invite_list(alice_backend_sock)
    assert rep == {"status": "ok", "invitations": []}

    rep = await invite_delete(
        alice_backend_sock, token=invitation.token, reason=InvitationDeletedReason.CANCELLED
    )
    assert rep == {"status": "not_found"}


@pytest.mark.trio
async def test_isolated_between_organizations(
    alice, otheralice, backend, backend_invited_sock_factory, alice_backend_sock
):
    invitation = await backend.invite.new_for_device(
        organization_id=otheralice.organization_id, greeter_user_id=otheralice.user_id
    )

    rep = await invite_list(alice_backend_sock)
    assert rep == {"status": "ok", "invitations": []}

    rep = await invite_delete(
        alice_backend_sock, token=invitation.token, reason=InvitationDeletedReason.CANCELLED
    )
    assert rep == {"status": "not_found"}

    with pytest.raises(HandshakeBadIdentity):
        async with backend_invited_sock_factory(
            backend,
            organization_id=alice.organization_id,
            invitation_type=InvitationType.DEVICE,
            token=invitation.token,
        ):
            pass
