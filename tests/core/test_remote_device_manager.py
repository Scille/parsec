# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum

from parsec.core.remote_devices_manager import RemoteDevicesManagerBackendOfflineError
from tests.common import freeze_time


@pytest.mark.trio
async def test_retrieve_device(running_backend, alice_remote_devices_manager, bob):
    remote_devices_manager = alice_remote_devices_manager
    d1 = Pendulum(2000, 1, 1)
    with freeze_time(d1):
        # Offline with no cache
        with pytest.raises(RemoteDevicesManagerBackendOfflineError):
            with running_backend.offline():
                await remote_devices_manager.get_device(bob.device_id)

        # Online
        device = await remote_devices_manager.get_device(bob.device_id)
        assert device.device_id == bob.device_id
        assert device.verify_key == bob.verify_key

        # Offline with cache
        with running_backend.offline():
            device2 = await remote_devices_manager.get_device(bob.device_id)
            assert device2 is device

    d2 = d1.add(remote_devices_manager.cache_validity + 1)
    with freeze_time(d2):
        # Offline with cache expired
        with pytest.raises(RemoteDevicesManagerBackendOfflineError):
            with running_backend.offline():
                await remote_devices_manager.get_device(bob.device_id)

        # Online with cache expired
        device = await remote_devices_manager.get_device(bob.device_id)
        assert device.device_id == bob.device_id
        assert device.verify_key == bob.verify_key


@pytest.mark.trio
async def test_retrieve_user(running_backend, alice_remote_devices_manager, bob):
    remote_devices_manager = alice_remote_devices_manager
    d1 = Pendulum(2000, 1, 1)
    with freeze_time(d1):
        # Offline with no cache
        with pytest.raises(RemoteDevicesManagerBackendOfflineError):
            with running_backend.offline():
                await remote_devices_manager.get_user(bob.user_id)

        # Online
        user, revoked_user = await remote_devices_manager.get_user(bob.user_id)
        assert user.user_id == bob.user_id
        assert user.public_key == bob.public_key
        assert revoked_user is None

        # Offline with cache
        with running_backend.offline():
            user2, revoked_user2 = await remote_devices_manager.get_user(bob.user_id)
            assert user2 is user
            assert revoked_user2 is None

    d2 = d1.add(remote_devices_manager.cache_validity + 1)
    with freeze_time(d2):
        # Offline with cache expired
        with pytest.raises(RemoteDevicesManagerBackendOfflineError):
            with running_backend.offline():
                await remote_devices_manager.get_user(bob.user_id)

        # Online with cache expired
        user, revoked_user = await remote_devices_manager.get_user(bob.user_id)
        assert user.user_id == bob.user_id
        assert user.public_key == bob.public_key
        assert revoked_user is None


@pytest.mark.trio
async def test_retrieve_user_and_devices(
    running_backend, alice_remote_devices_manager, alice, alice2
):
    remote_devices_manager = alice_remote_devices_manager
    d1 = Pendulum(2000, 1, 1)
    with freeze_time(d1):
        # Offline
        with pytest.raises(RemoteDevicesManagerBackendOfflineError):
            with running_backend.offline():
                await remote_devices_manager.get_user_and_devices(alice.user_id)

        # Online
        user, revoked_user, devices = await remote_devices_manager.get_user_and_devices(
            alice.user_id
        )
        assert user.user_id == alice.user_id
        assert user.public_key == alice.public_key
        assert revoked_user is None
        assert len(devices) == 2
        assert {d.device_id for d in devices} == {alice.device_id, alice2.device_id}

        # Offline (cache is never used)
        with pytest.raises(RemoteDevicesManagerBackendOfflineError):
            with running_backend.offline():
                await remote_devices_manager.get_user_and_devices(alice.user_id)
