# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

import pendulum
import pytest

from parsec.api.data import RealmRoleCertificateContent
from parsec.api.protocol import APIEvent, RealmRole
from parsec.backend.backend_events import BackendEvent
from tests.backend.common import realm_create, realm_update_roles
from tests.backend.test_events import events_listen_nowait, events_subscribe


@pytest.mark.trio
async def test_realm_create(backend, alice, alice_backend_sock):
    await events_subscribe(alice_backend_sock)

    realm_id = UUID("C0000000000000000000000000000000")
    certif = RealmRoleCertificateContent.build_realm_root_certif(
        author=alice.device_id, timestamp=pendulum.now(), realm_id=realm_id
    ).dump_and_sign(alice.signing_key)
    with backend.event_bus.listen() as spy:
        rep = await realm_create(alice_backend_sock, certif)
        assert rep == {"status": "ok"}
        await spy.wait_with_timeout(BackendEvent.REALM_ROLES_UPDATED)


@pytest.mark.trio
async def test_roles_updated_for_participant(
    backend, alice, bob, alice_backend_sock, bob_backend_sock, realm
):
    async def _update_role_and_check_events(role):

        with backend.event_bus.listen() as spy:
            certif = RealmRoleCertificateContent(
                author=alice.device_id,
                timestamp=pendulum.now(),
                realm_id=realm,
                user_id=bob.user_id,
                role=role,
            ).dump_and_sign(alice.signing_key)
            rep = await realm_update_roles(alice_backend_sock, certif, check_rep=False)
            assert rep == {"status": "ok"}

            await spy.wait_with_timeout(
                BackendEvent.REALM_ROLES_UPDATED,
                {
                    "organization_id": alice.organization_id,
                    "author": alice.device_id,
                    "realm_id": realm,
                    "user": bob.user_id,
                    "role": role,
                },
            )

        # Check events propagated to the client
        rep = await events_listen_nowait(bob_backend_sock)
        assert rep == {
            "status": "ok",
            "event": APIEvent.REALM_ROLES_UPDATED,
            "realm_id": realm,
            "role": role,
        }
        rep = await events_listen_nowait(bob_backend_sock)
        assert rep == {"status": "no_events"}

    # 0) Init event listening on the socket
    await events_subscribe(bob_backend_sock)

    # 1) New participant
    await _update_role_and_check_events(RealmRole.MANAGER)

    # 2) Change participant role
    await _update_role_and_check_events(RealmRole.READER)

    # 3) Stop sharing with participant
    await _update_role_and_check_events(None)
