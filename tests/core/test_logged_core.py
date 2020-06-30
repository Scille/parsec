# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pytest
import trio
from pendulum import Pendulum

from parsec.core import logged_core_factory
from parsec.core.backend_connection import (
    BackendConnStatus,
    BackendNotAvailable,
    BackendNotFoundError,
)
from parsec.core.core_events import CoreEvent
from parsec.core.types import DeviceInfo, UserInfo


@pytest.mark.trio
async def test_init_online_backend_late_reply(
    server_factory, core_config, alice, event_bus, backend
):
    can_serve_client = trio.Event()

    async def _handle_client(stream):
        await can_serve_client.wait()
        return await backend.handle_client(stream)

    async with server_factory(_handle_client, alice.organization_addr):
        with trio.fail_after(1):
            async with logged_core_factory(
                config=core_config, device=alice, event_bus=event_bus
            ) as core:
                # We don't want for backend to reply finish core init
                with core.event_bus.listen() as spy:
                    can_serve_client.set()
                    # Now backend reply, monitor should send events accordingly
                    await spy.wait(
                        CoreEvent.BACKEND_CONNECTION_CHANGED,
                        kwargs={"status": BackendConnStatus.READY, "status_exc": None},
                    )


@pytest.mark.trio
async def test_init_offline_backend_late_reply(server_factory, core_config, alice, event_bus):
    can_serve_client = trio.Event()

    async def _handle_client(stream):
        await can_serve_client.wait()
        await stream.aclose()

    async with server_factory(_handle_client, alice.organization_addr):
        with trio.fail_after(1):
            async with logged_core_factory(
                config=core_config, device=alice, event_bus=event_bus
            ) as core:
                with core.event_bus.listen() as spy:
                    can_serve_client.set()
                    await spy.wait(
                        CoreEvent.BACKEND_CONNECTION_CHANGED,
                        kwargs={"status": BackendConnStatus.LOST, "status_exc": ANY},
                    )


@pytest.mark.trio
async def test_find_and_get_info(running_backend, alice_core, bob, alice, alice2):
    infos, total = await alice_core.find_humans(query="bob")
    assert total == 1
    assert infos == [
        UserInfo(
            user_id=bob.user_id,
            human_handle=bob.human_handle,
            profile=bob.profile,
            created_on=Pendulum(2000, 1, 1),
            revoked_on=None,
        )
    ]

    info = await alice_core.get_user_info(bob.user_id)
    assert info == UserInfo(
        user_id=bob.user_id,
        human_handle=bob.human_handle,
        profile=bob.profile,
        created_on=Pendulum(2000, 1, 1),
        revoked_on=None,
    )

    infos = await alice_core.get_user_devices_info(bob.user_id)
    assert infos == [
        DeviceInfo(
            device_id=bob.device_id, device_label=bob.device_label, created_on=Pendulum(2000, 1, 1)
        )
    ]

    infos = await alice_core.get_user_devices_info()
    assert infos == [
        DeviceInfo(
            device_id=alice.device_id,
            device_label=alice.device_label,
            created_on=Pendulum(2000, 1, 1),
        ),
        DeviceInfo(
            device_id=alice2.device_id,
            device_label=alice2.device_label,
            created_on=Pendulum(2000, 1, 1),
        ),
    ]


@pytest.mark.trio
async def test_get_info_not_found(running_backend, alice_core, mallory):
    with pytest.raises(BackendNotFoundError):
        await alice_core.get_user_info(mallory.user_id)

    with pytest.raises(BackendNotFoundError):
        await alice_core.get_user_devices_info(mallory.user_id)


@pytest.mark.trio
async def test_find_get_info_and_revoke_offline(alice_core, bob):
    with pytest.raises(BackendNotAvailable):
        await alice_core.find_humans()

    with pytest.raises(BackendNotAvailable):
        await alice_core.get_user_info(bob.user_id)

    with pytest.raises(BackendNotAvailable):
        await alice_core.get_user_devices_info(bob.user_id)

    with pytest.raises(BackendNotAvailable):
        await alice_core.revoke_user(bob.user_id)


@pytest.mark.trio
@pytest.mark.parametrize("user_cached", [False, True])
async def test_revoke_user(running_backend, alice_core, bob, user_cached):
    if user_cached:
        before_info = await alice_core.get_user_info(bob.user_id)

    await alice_core.revoke_user(bob.user_id)

    after_info = await alice_core.get_user_info(bob.user_id)

    assert after_info.revoked_on
    if user_cached:
        assert after_info.user_id == before_info.user_id
        assert after_info.human_handle == before_info.human_handle
        assert after_info.profile == before_info.profile
        assert after_info.created_on == before_info.created_on
