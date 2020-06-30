# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pytest
import trio
from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data import PingMessageContent
from parsec.core.backend_connection import BackendConnStatus
from parsec.core.core_events import CoreEvent
from parsec.core.types import WorkspaceEntry, WorkspaceRole
from tests.common import create_shared_workspace, freeze_time


@pytest.mark.trio
async def test_monitors_idle(running_backend, alice_core):
    assert alice_core.are_monitors_idle()

    # Force wakeup of the message monitor
    alice_core.event_bus.send(CoreEvent.BACKEND_MESSAGE_RECEIVED, index=42)
    assert not alice_core.are_monitors_idle()
    await alice_core.wait_idle_monitors()
    assert alice_core.are_monitors_idle()


async def _send_msg(backend, author, recipient, ping="ping"):
    now = pendulum_now()
    message = PingMessageContent(author=author.device_id, timestamp=now, ping=ping)
    ciphered_message = message.dump_sign_and_encrypt_for(
        author_signkey=author.signing_key, recipient_pubkey=recipient.public_key
    )
    await backend.message.send(
        organization_id=recipient.organization_id,
        sender=author.device_id,
        recipient=recipient.user_id,
        timestamp=now,
        body=ciphered_message,
    )


@pytest.mark.trio
async def test_process_while_offline(
    mock_clock, running_backend, alice_core, bob_user_fs, alice, bob
):
    mock_clock.autojump_threshold = 0
    assert alice_core.backend_status == BackendConnStatus.READY

    with running_backend.offline():
        with alice_core.event_bus.listen() as spy:
            # Force wakeup of the message monitor
            alice_core.event_bus.send(CoreEvent.BACKEND_MESSAGE_RECEIVED, index=42)
            assert not alice_core.are_monitors_idle()

            with trio.fail_after(60):  # autojump, so not *really* 60s
                await spy.wait(
                    CoreEvent.BACKEND_CONNECTION_CHANGED,
                    {"status": BackendConnStatus.LOST, "status_exc": spy.ANY},
                )
                await alice_core.wait_idle_monitors()
            assert alice_core.backend_status == BackendConnStatus.LOST

        # Send message while alice is offline
        await _send_msg(
            backend=running_backend.backend, author=bob, recipient=alice, ping="hello from Bob !"
        )

    with alice_core.event_bus.listen() as spy:
        # Alice is back online, should retrieve Bob's message fine
        with trio.fail_after(60):  # autojump, so not *really* 60s
            await spy.wait(
                CoreEvent.BACKEND_CONNECTION_CHANGED,
                {"status": BackendConnStatus.READY, "status_exc": None},
            )
            await alice_core.wait_idle_monitors()
        assert alice_core.backend_status == BackendConnStatus.READY
        spy.assert_event_occured(CoreEvent.MESSAGE_PINGED, {"ping": "hello from Bob !"})


@pytest.mark.trio
async def test_new_sharing_trigger_event(alice_core, bob_core, running_backend):
    # First, create a folder and sync it on backend
    with freeze_time("2000-01-01"):
        wid = await alice_core.user_fs.workspace_create("foo")
    workspace = alice_core.user_fs.get_workspace(wid)
    with freeze_time("2000-01-02"):
        await workspace.sync()

    # Now we can share this workspace with Bob
    with bob_core.event_bus.listen() as spy:
        with freeze_time("2000-01-03"):
            await alice_core.user_fs.workspace_share(
                wid, recipient="bob", role=WorkspaceRole.MANAGER
            )

        # Bob should get a notification
        await spy.wait_with_timeout(
            CoreEvent.SHARING_UPDATED,
            {
                "new_entry": WorkspaceEntry(
                    name="foo",
                    id=wid,
                    key=ANY,
                    encryption_revision=1,
                    encrypted_on=Pendulum(2000, 1, 1),
                    role_cached_on=ANY,
                    role=WorkspaceRole.MANAGER,
                ),
                "previous_entry": None,
            },
        )


@pytest.mark.trio
async def test_revoke_sharing_trigger_event(alice_core, bob_core, running_backend):
    with freeze_time("2000-01-02"):
        wid = await create_shared_workspace("w", alice_core, bob_core)

    with bob_core.event_bus.listen() as spy:
        with freeze_time("2000-01-03"):
            await alice_core.user_fs.workspace_share(wid, recipient="bob", role=None)

        # Each workspace participant should get the message
        await spy.wait_with_timeout(
            CoreEvent.SHARING_UPDATED,
            {
                "new_entry": WorkspaceEntry(
                    name="w",
                    id=wid,
                    key=ANY,
                    encryption_revision=1,
                    encrypted_on=Pendulum(2000, 1, 2),
                    role_cached_on=ANY,
                    role=None,
                ),
                "previous_entry": WorkspaceEntry(
                    name="w",
                    id=wid,
                    key=ANY,
                    encryption_revision=1,
                    encrypted_on=Pendulum(2000, 1, 2),
                    role_cached_on=ANY,
                    role=WorkspaceRole.MANAGER,
                ),
            },
        )


@pytest.mark.trio
async def test_new_reencryption_trigger_event(alice_core, bob_core, running_backend):
    with freeze_time("2000-01-02"):
        wid = await create_shared_workspace("w", alice_core, bob_core)

    with alice_core.event_bus.listen() as aspy, bob_core.event_bus.listen() as bspy:
        with freeze_time("2000-01-03"):
            await alice_core.user_fs.workspace_start_reencryption(wid)

        # Each workspace participant should get the message
        await aspy.wait_with_timeout(
            CoreEvent.SHARING_UPDATED,
            {
                "new_entry": WorkspaceEntry(
                    name="w",
                    id=wid,
                    key=ANY,
                    encryption_revision=2,
                    encrypted_on=Pendulum(2000, 1, 3),
                    role_cached_on=ANY,
                    role=WorkspaceRole.OWNER,
                ),
                "previous_entry": WorkspaceEntry(
                    name="w",
                    id=wid,
                    key=ANY,
                    encryption_revision=1,
                    encrypted_on=Pendulum(2000, 1, 2),
                    role_cached_on=ANY,
                    role=WorkspaceRole.OWNER,
                ),
            },
        )
        await bspy.wait_with_timeout(
            CoreEvent.SHARING_UPDATED,
            {
                "new_entry": WorkspaceEntry(
                    name="w",
                    id=wid,
                    key=ANY,
                    encryption_revision=2,
                    encrypted_on=Pendulum(2000, 1, 3),
                    role_cached_on=ANY,
                    role=WorkspaceRole.MANAGER,
                ),
                "previous_entry": WorkspaceEntry(
                    name="w",
                    id=wid,
                    key=ANY,
                    encryption_revision=1,
                    encrypted_on=Pendulum(2000, 1, 2),
                    role_cached_on=ANY,
                    role=WorkspaceRole.MANAGER,
                ),
            },
        )
