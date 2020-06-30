# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID

import pytest
from pendulum import Pendulum

from parsec.api.protocol import APIEvent, RealmRole
from parsec.backend.backend_events import BackendEvent
from parsec.backend.realm import RealmGrantedRole
from tests.backend.common import events_listen_nowait, events_subscribe

NOW = Pendulum(2000, 1, 1)
VLOB_ID = UUID("00000000000000000000000000000001")
OTHER_VLOB_ID = UUID("00000000000000000000000000000002")
YET_ANOTHER_VLOB_ID = UUID("00000000000000000000000000000003")
REALM_ID = UUID("0000000000000000000000000000000A")


@pytest.mark.trio
async def test_vlobs_updated_event_ok(
    backend, alice_backend_sock, alice, alice2, realm, other_realm
):
    # Not listened events
    with backend.event_bus.listen() as spy:
        await backend.vlob.create(
            organization_id=alice.organization_id,
            author=alice.device_id,
            realm_id=realm,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            timestamp=NOW,
            blob=b"v1",
        )
        await spy.wait_with_timeout(BackendEvent.REALM_VLOBS_UPDATED)

    # Start listening events
    await events_subscribe(alice_backend_sock)

    # Good events
    with backend.event_bus.listen() as spy:

        await backend.vlob.create(
            organization_id=alice.organization_id,
            author=alice2.device_id,
            realm_id=other_realm,
            encryption_revision=1,
            vlob_id=OTHER_VLOB_ID,
            timestamp=NOW,
            blob=b"v1",
        )
        await backend.vlob.update(
            organization_id=alice.organization_id,
            author=alice2.device_id,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            version=2,
            timestamp=NOW,
            blob=b"v2",
        )
        await backend.vlob.update(
            organization_id=alice.organization_id,
            author=alice2.device_id,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            version=3,
            timestamp=NOW,
            blob=b"v3",
        )

        # No guarantees those events occur before the commands' return
        # On top of that, other `realm.vlobs_updated` has been triggered
        # before us (i.g. during alice user vlob creation). In case of slow
        # database those events could pop only now, hence shadowing the ones
        # we are waiting for. To avoid this we have to specify event params.
        await spy.wait_multiple_with_timeout(
            [
                (
                    BackendEvent.REALM_VLOBS_UPDATED,
                    {
                        "organization_id": alice2.organization_id,
                        "author": alice2.device_id,
                        "realm_id": other_realm,
                        "checkpoint": 1,
                        "src_id": OTHER_VLOB_ID,
                        "src_version": 1,
                    },
                ),
                (
                    BackendEvent.REALM_VLOBS_UPDATED,
                    {
                        "organization_id": alice2.organization_id,
                        "author": alice2.device_id,
                        "realm_id": realm,
                        "checkpoint": 2,
                        "src_id": VLOB_ID,
                        "src_version": 2,
                    },
                ),
                (
                    BackendEvent.REALM_VLOBS_UPDATED,
                    {
                        "organization_id": alice2.organization_id,
                        "author": alice2.device_id,
                        "realm_id": realm,
                        "checkpoint": 3,
                        "src_id": VLOB_ID,
                        "src_version": 3,
                    },
                ),
            ]
        )

    reps = [
        await events_listen_nowait(alice_backend_sock),
        await events_listen_nowait(alice_backend_sock),
        await events_listen_nowait(alice_backend_sock),
        await events_listen_nowait(alice_backend_sock),
    ]
    assert reps == [
        {
            "status": "ok",
            "event": APIEvent.REALM_VLOBS_UPDATED,
            "realm_id": other_realm,
            "checkpoint": 1,
            "src_id": OTHER_VLOB_ID,
            "src_version": 1,
        },
        {
            "status": "ok",
            "event": APIEvent.REALM_VLOBS_UPDATED,
            "realm_id": realm,
            "checkpoint": 2,
            "src_id": VLOB_ID,
            "src_version": 2,
        },
        {
            "status": "ok",
            "event": APIEvent.REALM_VLOBS_UPDATED,
            "realm_id": realm,
            "checkpoint": 3,
            "src_id": VLOB_ID,
            "src_version": 3,
        },
        {"status": "no_events"},
    ]


@pytest.mark.trio
async def test_vlobs_updated_event_handle_self_events(backend, alice_backend_sock, alice, realm):
    await events_subscribe(alice_backend_sock)

    with backend.event_bus.listen() as spy:

        await backend.vlob.create(
            organization_id=alice.organization_id,
            author=alice.device_id,
            realm_id=realm,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            timestamp=NOW,
            blob=b"v1",
        )

        await backend.vlob.create(
            organization_id=alice.organization_id,
            author=alice.device_id,
            realm_id=realm,
            encryption_revision=1,
            vlob_id=OTHER_VLOB_ID,
            timestamp=NOW,
            blob=b"v1",
        )
        await backend.vlob.update(
            organization_id=alice.organization_id,
            author=alice.device_id,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            version=2,
            timestamp=NOW,
            blob=b"v2",
        )

        # Wait for events to be processed by the backend
        await spy.wait_multiple_with_timeout(
            [
                BackendEvent.REALM_VLOBS_UPDATED,
                BackendEvent.REALM_VLOBS_UPDATED,
                BackendEvent.REALM_VLOBS_UPDATED,
            ]
        )

    # Self-events should have been ignored
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "no_events"}


@pytest.mark.trio
async def test_vlobs_updated_event_not_participant(backend, alice_backend_sock, bob, bob_realm):
    await events_subscribe(alice_backend_sock)

    with backend.event_bus.listen() as spy:

        await backend.vlob.create(
            organization_id=bob.organization_id,
            author=bob.device_id,
            realm_id=bob_realm,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            timestamp=NOW,
            blob=b"v1",
        )
        await backend.vlob.update(
            organization_id=bob.organization_id,
            author=bob.device_id,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            version=2,
            timestamp=NOW,
            blob=b"v2",
        )

        # Wait for events to be processed by the backend
        await spy.wait_multiple_with_timeout(
            [BackendEvent.REALM_VLOBS_UPDATED, BackendEvent.REALM_VLOBS_UPDATED]
        )

    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "no_events"}


@pytest.mark.trio
@pytest.mark.parametrize("realm_created_by_self", (True, False))
async def test_vlobs_updated_event_realm_created_after_subscribe(
    backend, alice_backend_sock, alice, alice2, realm, realm_created_by_self
):
    realm_id = UUID("0000000000000000000000000000000A")
    await events_subscribe(alice_backend_sock)

    # New realm, should get events anyway
    with backend.event_bus.listen() as spy:
        realm_creator = alice if realm_created_by_self else alice2
        # Create the realm
        await backend.realm.create(
            organization_id=realm_creator.organization_id,
            self_granted_role=RealmGrantedRole(
                realm_id=realm_id,
                user_id=realm_creator.user_id,
                certificate=b"<dummy>",
                role=RealmRole.OWNER,
                granted_by=realm_creator.device_id,
                granted_on=Pendulum(2000, 1, 2),
            ),
        )
        # Create vlob in realm
        await backend.vlob.create(
            organization_id=realm_creator.organization_id,
            author=realm_creator.device_id,
            realm_id=realm_id,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            timestamp=NOW,
            blob=b"v1",
        )
        # Update vlob in realm
        await backend.vlob.update(
            organization_id=alice2.organization_id,
            author=alice2.device_id,
            encryption_revision=1,
            vlob_id=VLOB_ID,
            version=2,
            timestamp=NOW,
            blob=b"v2",
        )

        # Wait for events to be processed by the backend
        await spy.wait_multiple_with_timeout(
            [
                BackendEvent.REALM_ROLES_UPDATED,
                BackendEvent.REALM_VLOBS_UPDATED,
                BackendEvent.REALM_VLOBS_UPDATED,
            ]
        )

    # Realm access granted
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "event": APIEvent.REALM_ROLES_UPDATED,
        "realm_id": realm_id,
        "role": RealmRole.OWNER,
    }

    # Create vlob in realm event
    if not realm_created_by_self:
        rep = await events_listen_nowait(alice_backend_sock)
        assert rep == {
            "status": "ok",
            "event": APIEvent.REALM_VLOBS_UPDATED,
            "realm_id": realm_id,
            "checkpoint": 1,
            "src_id": VLOB_ID,
            "src_version": 1,
        }

    # Update vlob in realm event
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "event": APIEvent.REALM_VLOBS_UPDATED,
        "realm_id": realm_id,
        "checkpoint": 2,
        "src_id": VLOB_ID,
        "src_version": 2,
    }

    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "no_events"}
