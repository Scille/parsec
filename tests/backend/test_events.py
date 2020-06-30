# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio

from parsec.api.protocol import APIEvent
from parsec.backend.backend_events import BackendEvent
from tests.backend.common import events_listen, events_listen_nowait, events_subscribe, ping


@pytest.mark.trio
async def test_events_subscribe(backend, alice_backend_sock, alice2_backend_sock):
    await events_subscribe(alice_backend_sock)

    # Should ignore our own events
    with backend.event_bus.listen() as spy:
        await ping(alice_backend_sock, "bar")
        await ping(alice2_backend_sock, "foo")

        # No guarantees those events occur before the commands' return
        await spy.wait_multiple_with_timeout([BackendEvent.PINGED, BackendEvent.PINGED])

    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "ok", "event": APIEvent.PINGED, "ping": "foo"}
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "no_events"}


@pytest.mark.trio
async def test_event_resubscribe(backend, alice_backend_sock, alice2_backend_sock):
    await events_subscribe(alice_backend_sock)

    with backend.event_bus.listen() as spy:
        await ping(alice2_backend_sock, "foo")

        # No guarantees those events occur before the commands' return
        await spy.wait_with_timeout(BackendEvent.PINGED)

    # Resubscribing should have no effect
    await events_subscribe(alice_backend_sock)

    with backend.event_bus.listen() as spy:
        await ping(alice2_backend_sock, "bar")
        await ping(alice2_backend_sock, "spam")

        # No guarantees those events occur before the commands' return
        await spy.wait_multiple_with_timeout([BackendEvent.PINGED, BackendEvent.PINGED])

    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "ok", "event": APIEvent.PINGED, "ping": "foo"}
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "ok", "event": APIEvent.PINGED, "ping": "bar"}
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "ok", "event": APIEvent.PINGED, "ping": "spam"}
    rep = await events_listen_nowait(alice_backend_sock)
    assert rep == {"status": "no_events"}


@pytest.mark.trio
@pytest.mark.postgresql
async def test_cross_backend_event(backend_factory, backend_sock_factory, alice, bob):
    async with backend_factory() as backend_1, backend_factory(populated=False) as backend_2:
        async with backend_sock_factory(backend_1, alice) as alice_sock, backend_sock_factory(
            backend_2, bob
        ) as bob_sock:

            await events_subscribe(alice_sock)

            async with events_listen(alice_sock) as listen:
                await ping(bob_sock, "foo")
            assert listen.rep == {"status": "ok", "event": APIEvent.PINGED, "ping": "foo"}

            await ping(bob_sock, "foo")

            # There is no guarantee an event is ready to be received once
            # the sender got it answer
            with trio.fail_after(1):
                while True:
                    rep = await events_listen_nowait(alice_sock)
                    if rep["status"] != "no_events":
                        break
                    await trio.sleep(0.1)
            assert rep == {"status": "ok", "event": APIEvent.PINGED, "ping": "foo"}

            rep = await events_listen_nowait(alice_sock)
            assert rep == {"status": "no_events"}


# TODO: test message.received and beacon.updated events
