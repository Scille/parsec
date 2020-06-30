# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum

from parsec.api.protocol import APIEvent, message_get_serializer
from parsec.backend.backend_events import BackendEvent
from parsec.backend.config import PostgreSQLBlockStoreConfig
from tests.backend.test_events import events_listen, events_listen_nowait, events_subscribe


async def message_get(sock, offset=0):
    await sock.send(message_get_serializer.req_dumps({"cmd": "message_get", "offset": offset}))
    raw_rep = await sock.recv()
    return message_get_serializer.rep_loads(raw_rep)


@pytest.mark.trio
async def test_message_from_bob_to_alice(backend, alice, bob, alice_backend_sock):
    await events_subscribe(alice_backend_sock)
    d1 = Pendulum(2000, 1, 1)
    async with events_listen(alice_backend_sock) as listen:
        await backend.message.send(
            bob.organization_id, bob.device_id, alice.user_id, d1, b"Hello from Bob !"
        )

    assert listen.rep == {"status": "ok", "event": APIEvent.MESSAGE_RECEIVED, "index": 1}

    rep = await message_get(alice_backend_sock)
    assert rep == {
        "status": "ok",
        "messages": [
            {"body": b"Hello from Bob !", "sender": bob.device_id, "timestamp": d1, "count": 1}
        ],
    }


@pytest.mark.trio
async def test_message_get_with_offset(backend, alice, bob, alice_backend_sock):
    d1 = Pendulum(2000, 1, 1)
    d2 = Pendulum(2000, 1, 2)
    await backend.message.send(bob.organization_id, bob.device_id, alice.user_id, d1, b"1")
    await backend.message.send(bob.organization_id, bob.device_id, alice.user_id, d1, b"2")
    await backend.message.send(bob.organization_id, bob.device_id, alice.user_id, d2, b"3")

    rep = await message_get(alice_backend_sock, 1)
    assert rep == {
        "status": "ok",
        "messages": [
            {"body": b"2", "sender": bob.device_id, "timestamp": d1, "count": 2},
            {"body": b"3", "sender": bob.device_id, "timestamp": d2, "count": 3},
        ],
    }


@pytest.mark.trio
@pytest.mark.postgresql
async def test_message_from_bob_to_alice_multi_backends(
    postgresql_url, alice, bob, backend_factory, backend_sock_factory
):
    d1 = Pendulum(2000, 1, 1)
    async with backend_factory(
        config={"blockstore_config": PostgreSQLBlockStoreConfig(), "db_url": postgresql_url}
    ) as backend_1, backend_factory(
        populated=False,
        config={"blockstore_config": PostgreSQLBlockStoreConfig(), "db_url": postgresql_url},
    ) as backend_2:

        async with backend_sock_factory(backend_1, alice) as alice_backend_sock:

            await events_subscribe(alice_backend_sock)
            async with events_listen(alice_backend_sock) as listen:
                await backend_2.message.send(
                    bob.organization_id, bob.device_id, alice.user_id, d1, b"Hello from Bob !"
                )

            assert listen.rep == {"status": "ok", "event": APIEvent.MESSAGE_RECEIVED, "index": 1}

            rep = await message_get(alice_backend_sock)
            assert rep == {
                "status": "ok",
                "messages": [
                    {
                        "body": b"Hello from Bob !",
                        "sender": bob.device_id,
                        "timestamp": d1,
                        "count": 1,
                    }
                ],
            }


@pytest.mark.trio
async def test_message_received_event(backend, alice_backend_sock, alice, bob):
    d1 = Pendulum(2000, 1, 1)
    await events_subscribe(alice_backend_sock)

    # Good message
    with backend.event_bus.listen() as spy:
        await backend.message.send(
            bob.organization_id, bob.device_id, alice.user_id, d1, b"Hello from bob to alice"
        )
        await backend.message.send(
            bob.organization_id, bob.device_id, alice.user_id, d1, b"Goodbye from bob to alice"
        )

        # No guarantees those events occur before the commands' return
        await spy.wait_multiple_with_timeout(
            [BackendEvent.MESSAGE_RECEIVED, BackendEvent.MESSAGE_RECEIVED]
        )

    reps = [
        await events_listen_nowait(alice_backend_sock),
        await events_listen_nowait(alice_backend_sock),
        await events_listen_nowait(alice_backend_sock),
    ]
    assert reps == [
        {"status": "ok", "event": APIEvent.MESSAGE_RECEIVED, "index": 1},
        {"status": "ok", "event": APIEvent.MESSAGE_RECEIVED, "index": 2},
        {"status": "no_events"},
    ]

    # Message to self also trigger event (not as silly as at sound: see workspace reencryption)
    with backend.event_bus.listen() as spy:
        await backend.message.send(
            alice.organization_id, alice.device_id, alice.user_id, d1, b"Hello to myself"
        )

        # No guarantees those events occur before the commands' return
        await spy.wait_multiple_with_timeout([BackendEvent.MESSAGE_RECEIVED])

    reps = [
        await events_listen_nowait(alice_backend_sock),
        await events_listen_nowait(alice_backend_sock),
    ]
    assert reps == [
        {"status": "ok", "event": APIEvent.MESSAGE_RECEIVED, "index": 3},
        {"status": "no_events"},
    ]
