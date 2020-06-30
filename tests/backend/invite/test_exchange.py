# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio
from pendulum import Pendulum

from parsec.api.protocol import InvitationType
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
)


@pytest.fixture
async def invitation(backend, alice):
    invitation = await backend.invite.new_for_device(
        organization_id=alice.organization_id,
        greeter_user_id=alice.user_id,
        created_on=Pendulum(2000, 1, 2),
    )
    return invitation


@pytest.fixture
async def invited_sock(backend, backend_invited_sock_factory, alice, invitation):
    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
    ) as invited_sock:
        yield invited_sock


class PeerControler:
    def __init__(self):
        self._orders_sender, self._orders_receiver = trio.open_memory_channel(0)
        self._orders_ack_sender, self._orders_ack_receiver = trio.open_memory_channel(0)
        self._results_sender, self._results_receiver = trio.open_memory_channel(1)

    async def send_order(self, order, order_arg=None):
        assert self._results_receiver.statistics().current_buffer_used == 0
        await self._orders_sender.send((order, order_arg))
        await self._orders_ack_receiver.receive()

    async def get_result(self):
        return await self._results_receiver.receive()

    async def assert_ok_rep(self):
        rep = await self.get_result()
        assert rep["status"] == "ok"

    async def peer_do(self, action, *args, **kwargs):
        print("START", action.cmd)
        async with action.async_call(*args, **kwargs) as async_rep:
            print("DONE", action.cmd)
            await self._orders_ack_sender.send(None)
        print("REP", action.cmd, async_rep.rep)
        await self._results_sender.send(async_rep.rep)
        return True

    async def peer_next_order(self):
        return await self._orders_receiver.receive()


@pytest.fixture
async def exchange_testbed(alice_backend_sock, invitation, invited_sock):
    greeter_privkey = PrivateKey.generate()
    claimer_privkey = PrivateKey.generate()

    async def _run_greeter(peer_controller):
        while True:
            order, order_arg = await peer_controller.peer_next_order()

            if order == "1_wait_peer":
                await peer_controller.peer_do(
                    invite_1_greeter_wait_peer,
                    alice_backend_sock,
                    token=invitation.token,
                    greeter_public_key=greeter_privkey.public_key,
                )

            elif order == "2a_get_hashed_nonce":
                await peer_controller.peer_do(
                    invite_2a_greeter_get_hashed_nonce, alice_backend_sock, token=invitation.token
                )

            elif order == "2b_send_nonce":
                await peer_controller.peer_do(
                    invite_2b_greeter_send_nonce,
                    alice_backend_sock,
                    token=invitation.token,
                    greeter_nonce=b"<greeter_nonce>",
                )

            elif order == "3a_wait_peer_trust":
                await peer_controller.peer_do(
                    invite_3a_greeter_wait_peer_trust, alice_backend_sock, token=invitation.token
                )

            elif order == "3b_signify_trust":
                await peer_controller.peer_do(
                    invite_3b_greeter_signify_trust, alice_backend_sock, token=invitation.token
                )

            elif order == "4_communicate":
                await peer_controller.peer_do(
                    invite_4_greeter_communicate,
                    alice_backend_sock,
                    token=invitation.token,
                    payload=order_arg,
                )

            else:
                assert False

    async def _run_claimer(peer_controller):
        while True:
            order, order_arg = await peer_controller.peer_next_order()

            if order == "1_wait_peer":
                await peer_controller.peer_do(
                    invite_1_claimer_wait_peer,
                    invited_sock,
                    claimer_public_key=claimer_privkey.public_key,
                )

            elif order == "2a_send_hashed_nonce":
                await peer_controller.peer_do(
                    invite_2a_claimer_send_hashed_nonce,
                    invited_sock,
                    claimer_hashed_nonce=b"<claimer_hashed_nonce>",
                )

            elif order == "2b_send_nonce":
                await peer_controller.peer_do(
                    invite_2b_claimer_send_nonce, invited_sock, claimer_nonce=b"<claimer_nonce>"
                )

            elif order == "3a_signify_trust":
                await peer_controller.peer_do(invite_3a_claimer_signify_trust, invited_sock)

            elif order == "3b_wait_peer_trust":
                await peer_controller.peer_do(invite_3b_claimer_wait_peer_trust, invited_sock)

            elif order == "4_communicate":
                assert order_arg is not None
                await peer_controller.peer_do(
                    invite_4_claimer_communicate, invited_sock, payload=order_arg
                )

            else:
                assert False

    greeter_ctlr = PeerControler()
    claimer_ctlr = PeerControler()
    async with trio.open_nursery() as nursery:
        nursery.start_soon(_run_greeter, greeter_ctlr)
        nursery.start_soon(_run_claimer, claimer_ctlr)

        yield greeter_privkey, claimer_privkey, greeter_ctlr, claimer_ctlr

        nursery.cancel_scope.cancel()


@pytest.mark.trio
@pytest.mark.parametrize("leader", ("claimer", "greeter"))
async def test_conduit_exchange_good(exchange_testbed, leader):
    greeter_privkey, claimer_privkey, greeter_ctlr, claimer_ctlr = exchange_testbed

    # Step 1
    if leader == "greeter":
        await greeter_ctlr.send_order("1_wait_peer")
        await claimer_ctlr.send_order("1_wait_peer")
    else:
        await claimer_ctlr.send_order("1_wait_peer")
        await greeter_ctlr.send_order("1_wait_peer")
    greeter_rep = await greeter_ctlr.get_result()
    claimer_rep = await claimer_ctlr.get_result()
    assert greeter_rep == {"status": "ok", "claimer_public_key": claimer_privkey.public_key}
    assert claimer_rep == {"status": "ok", "greeter_public_key": greeter_privkey.public_key}

    # Step 2
    if leader == "greeter":
        await greeter_ctlr.send_order("2a_get_hashed_nonce")
        await claimer_ctlr.send_order("2a_send_hashed_nonce")
    else:
        await claimer_ctlr.send_order("2a_send_hashed_nonce")
        await greeter_ctlr.send_order("2a_get_hashed_nonce")

    greeter_rep = await greeter_ctlr.get_result()
    assert greeter_rep == {"status": "ok", "claimer_hashed_nonce": b"<claimer_hashed_nonce>"}
    await greeter_ctlr.send_order("2b_send_nonce")

    claimer_rep = await claimer_ctlr.get_result()
    assert claimer_rep == {"status": "ok", "greeter_nonce": b"<greeter_nonce>"}
    await claimer_ctlr.send_order("2b_send_nonce")

    greeter_rep = await greeter_ctlr.get_result()
    assert greeter_rep == {"status": "ok", "claimer_nonce": b"<claimer_nonce>"}
    claimer_rep = await claimer_ctlr.get_result()
    assert claimer_rep == {"status": "ok"}

    # Step 3a
    if leader == "greeter":
        await greeter_ctlr.send_order("3a_wait_peer_trust")
        await claimer_ctlr.send_order("3a_signify_trust")
    else:
        await claimer_ctlr.send_order("3a_signify_trust")
        await greeter_ctlr.send_order("3a_wait_peer_trust")
    greeter_rep = await greeter_ctlr.get_result()
    assert greeter_rep == {"status": "ok"}
    claimer_rep = await claimer_ctlr.get_result()
    assert claimer_rep == {"status": "ok"}

    # Step 3b
    if leader == "greeter":
        await greeter_ctlr.send_order("3b_signify_trust")
        await claimer_ctlr.send_order("3b_wait_peer_trust")
    else:
        await claimer_ctlr.send_order("3b_wait_peer_trust")
        await greeter_ctlr.send_order("3b_signify_trust")
    greeter_rep = await greeter_ctlr.get_result()
    assert greeter_rep == {"status": "ok"}
    claimer_rep = await claimer_ctlr.get_result()
    assert claimer_rep == {"status": "ok"}

    # Step 4
    if leader == "greeter":
        await greeter_ctlr.send_order("4_communicate", b"hello from greeter")
        await claimer_ctlr.send_order("4_communicate", b"hello from claimer")
    else:
        await claimer_ctlr.send_order("4_communicate", b"hello from claimer")
        await greeter_ctlr.send_order("4_communicate", b"hello from greeter")
    greeter_rep = await greeter_ctlr.get_result()
    assert greeter_rep == {"status": "ok", "payload": b"hello from claimer"}
    claimer_rep = await claimer_ctlr.get_result()
    assert claimer_rep == {"status": "ok", "payload": b"hello from greeter"}

    if leader == "greeter":
        await greeter_ctlr.send_order("4_communicate", b"")
        await claimer_ctlr.send_order("4_communicate", b"")
    else:
        await claimer_ctlr.send_order("4_communicate", b"")
        await greeter_ctlr.send_order("4_communicate", b"")
    greeter_rep = await greeter_ctlr.get_result()
    assert greeter_rep == {"status": "ok", "payload": b""}
    claimer_rep = await claimer_ctlr.get_result()
    assert claimer_rep == {"status": "ok", "payload": b""}


@pytest.mark.trio
async def test_conduit_exchange_reset(exchange_testbed):
    greeter_privkey, claimer_privkey, greeter_ctlr, claimer_ctlr = exchange_testbed

    # Step 1
    await greeter_ctlr.send_order("1_wait_peer")
    await claimer_ctlr.send_order("1_wait_peer")
    await greeter_ctlr.assert_ok_rep()
    await claimer_ctlr.assert_ok_rep()

    # Claimer reset just before step 2a
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("1_wait_peer")
            await greeter_ctlr.send_order("2a_get_hashed_nonce")
        else:
            await greeter_ctlr.send_order("2a_get_hashed_nonce")
            await claimer_ctlr.send_order("1_wait_peer")
        greeter_rep = await greeter_ctlr.get_result()
        assert greeter_rep == {"status": "invalid_state"}
        await greeter_ctlr.send_order("1_wait_peer")
        await greeter_ctlr.assert_ok_rep()
        await claimer_ctlr.assert_ok_rep()

    # Greeter reset just before step 2a
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("2a_send_hashed_nonce")
            await greeter_ctlr.send_order("1_wait_peer")
        else:
            await greeter_ctlr.send_order("1_wait_peer")
            await claimer_ctlr.send_order("2a_send_hashed_nonce")
        claimer_rep = await claimer_ctlr.get_result()
        assert claimer_rep == {"status": "invalid_state"}
        await claimer_ctlr.send_order("1_wait_peer")
        await claimer_ctlr.assert_ok_rep()
        await greeter_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    greeter_rep = await greeter_ctlr.assert_ok_rep()
    # Greeter reset after retrieving claimer hashed nonce
    await greeter_ctlr.send_order("1_wait_peer")
    claimer_rep = await claimer_ctlr.get_result()
    assert claimer_rep == {"status": "invalid_state"}
    await claimer_ctlr.send_order("1_wait_peer")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    await greeter_ctlr.assert_ok_rep()
    # Step 2b
    await greeter_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    # Claimer reset after retrieving greeter nonce
    await claimer_ctlr.send_order("1_wait_peer")
    greeter_rep = await greeter_ctlr.get_result()
    assert greeter_rep == {"status": "invalid_state"}
    await greeter_ctlr.send_order("1_wait_peer")
    await greeter_ctlr.assert_ok_rep()
    await claimer_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    await greeter_ctlr.assert_ok_rep()
    # Step 2b
    await greeter_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await claimer_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Greeter reset just before step 3a
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("3a_signify_trust")
            await greeter_ctlr.send_order("1_wait_peer")
        else:
            await greeter_ctlr.send_order("1_wait_peer")
            await claimer_ctlr.send_order("3a_signify_trust")
        claimer_rep = await claimer_ctlr.get_result()
        assert claimer_rep == {"status": "invalid_state"}
        await claimer_ctlr.send_order("1_wait_peer")
        await claimer_ctlr.assert_ok_rep()
        await greeter_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    await greeter_ctlr.assert_ok_rep()
    # Step 2b
    await greeter_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await claimer_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Claimer reset just before step 3a
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("1_wait_peer")
            await greeter_ctlr.send_order("3a_wait_peer_trust")
        else:
            await greeter_ctlr.send_order("3a_wait_peer_trust")
            await claimer_ctlr.send_order("1_wait_peer")
        greeter_rep = await greeter_ctlr.get_result()
        assert greeter_rep == {"status": "invalid_state"}
        await greeter_ctlr.send_order("1_wait_peer")
        await greeter_ctlr.assert_ok_rep()
        await claimer_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    await greeter_ctlr.assert_ok_rep()
    # Step 2b
    await greeter_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await claimer_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Step 3a
    await greeter_ctlr.send_order("3a_wait_peer_trust")
    await claimer_ctlr.send_order("3a_signify_trust")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Greeter reset just before step 3b
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("3b_wait_peer_trust")
            await greeter_ctlr.send_order("1_wait_peer")
        else:
            await greeter_ctlr.send_order("1_wait_peer")
            await claimer_ctlr.send_order("3b_wait_peer_trust")
        claimer_rep = await claimer_ctlr.get_result()
        assert claimer_rep == {"status": "invalid_state"}
        await claimer_ctlr.send_order("1_wait_peer")
        await claimer_ctlr.assert_ok_rep()
        await greeter_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    await greeter_ctlr.assert_ok_rep()
    # Step 2b
    await greeter_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await claimer_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Step 3a
    await greeter_ctlr.send_order("3a_wait_peer_trust")
    await claimer_ctlr.send_order("3a_signify_trust")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Claimer reset just before step 3b
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("1_wait_peer")
            await greeter_ctlr.send_order("3b_signify_trust")
        else:
            await greeter_ctlr.send_order("3b_signify_trust")
            await claimer_ctlr.send_order("1_wait_peer")
        greeter_rep = await greeter_ctlr.get_result()
        assert greeter_rep == {"status": "invalid_state"}
        await greeter_ctlr.send_order("1_wait_peer")
        await greeter_ctlr.assert_ok_rep()
        await claimer_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    await greeter_ctlr.assert_ok_rep()
    # Step 2b
    await greeter_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await claimer_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Step 3a
    await greeter_ctlr.send_order("3a_wait_peer_trust")
    await claimer_ctlr.send_order("3a_signify_trust")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Step 3b
    await greeter_ctlr.send_order("3b_signify_trust")
    await claimer_ctlr.send_order("3b_wait_peer_trust")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Greeter reset just before step 4
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("4_communicate", b"")
            await greeter_ctlr.send_order("1_wait_peer")
        else:
            await greeter_ctlr.send_order("1_wait_peer")
            await claimer_ctlr.send_order("4_communicate", b"")
        claimer_rep = await claimer_ctlr.get_result()
        assert claimer_rep == {"status": "invalid_state"}
        await claimer_ctlr.send_order("1_wait_peer")
        await claimer_ctlr.assert_ok_rep()
        await greeter_ctlr.assert_ok_rep()

    # Step 2a
    await greeter_ctlr.send_order("2a_get_hashed_nonce")
    await claimer_ctlr.send_order("2a_send_hashed_nonce")
    await greeter_ctlr.assert_ok_rep()
    # Step 2b
    await greeter_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await claimer_ctlr.send_order("2b_send_nonce")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Step 3a
    await greeter_ctlr.send_order("3a_wait_peer_trust")
    await claimer_ctlr.send_order("3a_signify_trust")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Step 3b
    await greeter_ctlr.send_order("3b_signify_trust")
    await claimer_ctlr.send_order("3b_wait_peer_trust")
    await claimer_ctlr.assert_ok_rep()
    await greeter_ctlr.assert_ok_rep()
    # Claimer reset just before step 4
    for leader in ("claimer", "greeter"):
        if leader == "claimer":
            await claimer_ctlr.send_order("1_wait_peer")
            await greeter_ctlr.send_order("4_communicate", b"")
        else:
            await greeter_ctlr.send_order("4_communicate", b"")
            await claimer_ctlr.send_order("1_wait_peer")
        greeter_rep = await greeter_ctlr.get_result()
        assert greeter_rep == {"status": "invalid_state"}
        await greeter_ctlr.send_order("1_wait_peer")
        await greeter_ctlr.assert_ok_rep()
        await claimer_ctlr.assert_ok_rep()


@pytest.mark.trio
async def test_claimer_step_1_retry(
    backend, alice, backend_invited_sock_factory, alice_backend_sock, invitation
):
    greeter_privkey = PrivateKey.generate()
    claimer_privkey = PrivateKey.generate()

    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
    ) as invited_sock:
        with backend.event_bus.listen() as spy:
            with trio.CancelScope() as cancel_scope:
                async with invite_1_claimer_wait_peer.async_call(
                    invited_sock, claimer_public_key=claimer_privkey.public_key
                ):
                    await spy.wait_with_timeout(
                        BackendEvent.INVITE_CONDUIT_UPDATED,
                        kwargs={
                            "organization_id": alice.organization_id,
                            "token": invitation.token,
                        },
                    )
                    # Here greeter is waiting for claimer, that the time we choose to close greeter connection
                    cancel_scope.cancel()

    # Now retry the first step with a new connection
    async with backend_invited_sock_factory(
        backend,
        organization_id=alice.organization_id,
        invitation_type=InvitationType.DEVICE,
        token=invitation.token,
    ) as invited_sock:
        with trio.fail_after(1):
            with backend.event_bus.listen() as spy:
                async with invite_1_claimer_wait_peer.async_call(
                    invited_sock, claimer_public_key=claimer_privkey.public_key
                ) as claimer_async_rep:
                    # Must wait for the reset command to update the conduit
                    # before starting the greeter command otherwise it will
                    # also be reseted
                    await spy.wait_with_timeout(
                        BackendEvent.INVITE_CONDUIT_UPDATED,
                        kwargs={
                            "organization_id": alice.organization_id,
                            "token": invitation.token,
                        },
                    )
                    greeter_rep = await invite_1_greeter_wait_peer(
                        alice_backend_sock,
                        token=invitation.token,
                        greeter_public_key=greeter_privkey.public_key,
                    )
                assert greeter_rep == {
                    "status": "ok",
                    "claimer_public_key": claimer_privkey.public_key,
                }
            assert claimer_async_rep.rep == {
                "status": "ok",
                "greeter_public_key": greeter_privkey.public_key,
            }


@pytest.mark.trio
async def test_claimer_step_2_retry(
    backend, alice, backend_sock_factory, alice_backend_sock, invitation, invited_sock
):
    greeter_privkey = PrivateKey.generate()
    claimer_privkey = PrivateKey.generate()
    greeter_retry_privkey = PrivateKey.generate()
    claimer_retry_privkey = PrivateKey.generate()

    # Step 1
    with trio.fail_after(1):
        async with invite_1_greeter_wait_peer.async_call(
            alice_backend_sock,
            token=invitation.token,
            greeter_public_key=greeter_privkey.public_key,
        ) as greeter_async_rep:
            claimer_rep = await invite_1_claimer_wait_peer(
                invited_sock, claimer_public_key=claimer_privkey.public_key
            )
            assert claimer_rep == {"status": "ok", "greeter_public_key": greeter_privkey.public_key}
        assert greeter_async_rep.rep == {
            "status": "ok",
            "claimer_public_key": claimer_privkey.public_key,
        }

    # Greeter initiates step 2a...
    with trio.fail_after(1):
        with backend.event_bus.listen() as spy:

            async with invite_2a_greeter_get_hashed_nonce.async_call(
                alice_backend_sock, token=invitation.token
            ) as greeter_2a_async_rep:
                await spy.wait_with_timeout(
                    BackendEvent.INVITE_CONDUIT_UPDATED,
                    kwargs={"organization_id": alice.organization_id, "token": invitation.token},
                )

                # ...but changes his mind and reset from another connection !
                async with backend_sock_factory(backend, alice) as alice_backend_sock2:
                    async with invite_1_greeter_wait_peer.async_call(
                        alice_backend_sock2,
                        token=invitation.token,
                        greeter_public_key=greeter_retry_privkey.public_key,
                    ) as greeter_retry_1_async_rep:

                        # First connection should be notified of the reset
                        await greeter_2a_async_rep.do_recv()
                        assert greeter_2a_async_rep.rep == {"status": "invalid_state"}

                        # Claimer now arrives and try to do step 2a
                        rep = await invite_2a_claimer_send_hashed_nonce(
                            invited_sock, claimer_hashed_nonce=b"hashed nonce"
                        )
                        assert rep == {"status": "invalid_state"}

                        # So claimer returns to step 1
                        rep = await invite_1_claimer_wait_peer(
                            invited_sock, claimer_public_key=claimer_retry_privkey.public_key
                        )
                        assert rep == {
                            "status": "ok",
                            "greeter_public_key": greeter_retry_privkey.public_key,
                        }

                    assert greeter_retry_1_async_rep.rep == {
                        "status": "ok",
                        "claimer_public_key": claimer_retry_privkey.public_key,
                    }

            # Finally retry and achieve step 2

            async def _claimer_step_2():
                rep = await invite_2a_greeter_get_hashed_nonce(
                    alice_backend_sock, token=invitation.token
                )
                assert rep == {"status": "ok", "claimer_hashed_nonce": b"retry hashed nonce"}
                rep = await invite_2b_greeter_send_nonce(
                    alice_backend_sock, token=invitation.token, greeter_nonce=b"greeter nonce"
                )
                assert rep == {"status": "ok", "claimer_nonce": b"claimer nonce"}

            async def _greeter_step_2():
                rep = await invite_2a_claimer_send_hashed_nonce(
                    invited_sock, claimer_hashed_nonce=b"retry hashed nonce"
                )
                assert rep == {"status": "ok", "greeter_nonce": b"greeter nonce"}
                rep = await invite_2b_claimer_send_nonce(
                    invited_sock, claimer_nonce=b"claimer nonce"
                )
                assert rep == {"status": "ok"}

            with trio.fail_after(1):
                async with trio.open_nursery() as nursery:
                    nursery.start_soon(_claimer_step_2)
                    nursery.start_soon(_greeter_step_2)
