# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from uuid import uuid4
from pendulum import now as pendulum_now

from parsec.backend.stats import OrganizationStats, UserStats, DeviceStats, RealmStats

from tests.common import freeze_time
from tests.backend.common import organization_stats, realm_stats


@pytest.fixture
def generator(backend, backend_data_binder_factory, local_device_factory, backend_sock_factory):
    binder = backend_data_binder_factory(backend)

    class StatsTestbed:
        @staticmethod
        async def add_vlob(data_size, author, realm_id=None):
            await backend.vlob.create(
                organization_id=author.organization_id,
                author=author.device_id,
                encryption_revision=1,
                timestamp=pendulum_now(),
                realm_id=realm_id or author.user_manifest_id,
                vlob_id=uuid4(),
                blob=b"0" * data_size,
            )

        @staticmethod
        async def add_block(data_size, author, realm_id=None):
            await backend.block.create(
                organization_id=author.organization_id,
                author=author.device_id,
                realm_id=realm_id or author.user_manifest_id,
                block_id=uuid4(),
                block=b"0" * data_size,
            )

        @staticmethod
        async def add_device(org, base_device_id=None):
            new_device = local_device_factory(org=org, base_device_id=base_device_id)
            # use `initial_user_manifest_in_v0` so we don't create new vlobs
            await binder.bind_device(device=new_device, initial_user_manifest_in_v0=True)
            return new_device

        @staticmethod
        async def update_last_connected(device):
            async with backend_sock_factory(backend, device):
                pass

    return StatsTestbed()


@pytest.fixture
async def organization_stats_testbed(backend, coolorg, alice_backend_sock):
    stats = await backend.stats.organization_stats(organization_id=coolorg.organization_id)

    class OrganizationStatsTestbed:
        def __init__(self):
            self.expected_last_connected_on = stats.last_connected_on
            self.expected_last_connected_by = stats.last_connected_by
            self.expected_users_count = stats.users_count
            self.expected_devices_count = stats.devices_count
            self.expected_vlobs_size = stats.vlobs_size
            self.expected_vlobs_count = stats.vlobs_count
            self.expected_blocks_size = stats.blocks_size
            self.expected_blocks_count = stats.blocks_count

        async def assert_stats(self):
            stats = await backend.stats.organization_stats(organization_id=coolorg.organization_id)
            assert stats == OrganizationStats(
                last_connected_on=self.expected_last_connected_on,
                last_connected_by=self.expected_last_connected_by,
                users_count=self.expected_users_count,
                devices_count=self.expected_devices_count,
                vlobs_size=self.expected_vlobs_size,
                vlobs_count=self.expected_vlobs_count,
                blocks_size=self.expected_blocks_size,
                blocks_count=self.expected_blocks_count,
            )
            rep = await organization_stats(alice_backend_sock)
            assert rep == {
                "status": "ok",
                "users": self.expected_users_count,
                "metadata_size": self.expected_vlobs_size,
                "data_size": self.expected_blocks_size,
            }

    return OrganizationStatsTestbed()


@pytest.fixture
async def user_stats_testbed(backend, alice, alice_backend_sock):
    # Depend on `alice_backend_sock` to ensure we get the correct last connection
    stats = await backend.stats.user_stats(
        organization_id=alice.organization_id, user_id=alice.user_id
    )

    class UserStatsTestbed:
        def __init__(self):
            self.expected_last_connected_on = stats.last_connected_on
            self.expected_last_connected_by = stats.last_connected_by
            self.expected_devices_count = stats.devices_count
            self.expected_vlobs_size = stats.vlobs_size
            self.expected_vlobs_count = stats.vlobs_count
            self.expected_blocks_size = stats.blocks_size
            self.expected_blocks_count = stats.blocks_count

        async def assert_stats(self):
            stats = await backend.stats.user_stats(
                organization_id=alice.organization_id, user_id=alice.user_id
            )
            assert stats == UserStats(
                last_connected_on=self.expected_last_connected_on,
                last_connected_by=self.expected_last_connected_by,
                devices_count=self.expected_devices_count,
                vlobs_size=self.expected_vlobs_size,
                vlobs_count=self.expected_vlobs_count,
                blocks_size=self.expected_blocks_size,
                blocks_count=self.expected_blocks_count,
            )

    return UserStatsTestbed()


@pytest.fixture
async def device_stats_testbed(backend, alice, alice_backend_sock):
    # Depend on `alice_backend_sock` to ensure we get the correct last connection
    stats = await backend.stats.device_stats(
        organization_id=alice.organization_id, device_id=alice.device_id
    )

    class DeviceStatsTestbed:
        def __init__(self):
            self.expected_last_connected_on = stats.last_connected_on
            self.expected_vlobs_size = stats.vlobs_size
            self.expected_vlobs_count = stats.vlobs_count
            self.expected_blocks_size = stats.blocks_size
            self.expected_blocks_count = stats.blocks_count

        async def assert_stats(self):
            stats = await backend.stats.device_stats(
                organization_id=alice.organization_id, device_id=alice.device_id
            )
            assert stats == DeviceStats(
                last_connected_on=self.expected_last_connected_on,
                vlobs_size=self.expected_vlobs_size,
                vlobs_count=self.expected_vlobs_count,
                blocks_size=self.expected_blocks_size,
                blocks_count=self.expected_blocks_count,
            )

    return DeviceStatsTestbed()


@pytest.fixture
async def realm_stats_testbed(backend, coolorg, realm, alice_backend_sock):
    stats = await backend.stats.realm_stats(organization_id=coolorg.organization_id, realm_id=realm)

    class RealmStatsTestbed:
        def __init__(self):
            self.expected_vlobs_size = stats.vlobs_size
            self.expected_vlobs_count = stats.vlobs_count
            self.expected_blocks_size = stats.blocks_size
            self.expected_blocks_count = stats.blocks_count

        async def assert_stats(self):
            stats = await backend.stats.realm_stats(
                organization_id=coolorg.organization_id, realm_id=realm
            )
            assert stats == RealmStats(
                vlobs_size=self.expected_vlobs_size,
                vlobs_count=self.expected_vlobs_count,
                blocks_size=self.expected_blocks_size,
                blocks_count=self.expected_blocks_count,
            )
            rep = await realm_stats(alice_backend_sock, realm_id=realm)
            assert rep == {
                "status": "ok",
                "vlobs_size": self.expected_vlobs_size,
                "blocks_size": self.expected_blocks_size,
            }

    return RealmStatsTestbed()


@pytest.mark.trio
async def test_stats_megatest(
    coolorg,
    otherorg,
    alice,
    alice2,
    otheralice,
    realm,
    generator,
    organization_stats_testbed,
    user_stats_testbed,
    device_stats_testbed,
    realm_stats_testbed,
):
    # We cannot just use a fix 2000-01-02 mock date when freezing time given
    # `alice_backend_sock` fixture has already registered a last connection
    # with current date.
    next_year = pendulum_now().year + 1

    async def assert_stats():
        await organization_stats_testbed.assert_stats()
        await user_stats_testbed.assert_stats()
        await device_stats_testbed.assert_stats()
        await realm_stats_testbed.assert_stats()

    # 1 - Test stats on metadata

    await generator.add_vlob(data_size=4, author=alice, realm_id=realm)
    organization_stats_testbed.expected_vlobs_size += 4
    organization_stats_testbed.expected_vlobs_count += 1
    user_stats_testbed.expected_vlobs_size += 4
    user_stats_testbed.expected_vlobs_count += 1
    device_stats_testbed.expected_vlobs_size += 4
    device_stats_testbed.expected_vlobs_count += 1
    realm_stats_testbed.expected_vlobs_count += 1
    realm_stats_testbed.expected_vlobs_size += 4

    await generator.add_vlob(data_size=4, author=alice2, realm_id=realm)
    organization_stats_testbed.expected_vlobs_size += 4
    organization_stats_testbed.expected_vlobs_count += 1
    user_stats_testbed.expected_vlobs_size += 4
    user_stats_testbed.expected_vlobs_count += 1
    realm_stats_testbed.expected_vlobs_size += 4
    realm_stats_testbed.expected_vlobs_count += 1

    await generator.add_vlob(data_size=4, author=otheralice)

    await assert_stats()

    # 2 - Test stats on data

    await generator.add_block(data_size=4, author=alice, realm_id=realm)
    organization_stats_testbed.expected_blocks_size += 4
    organization_stats_testbed.expected_blocks_count += 1
    user_stats_testbed.expected_blocks_size += 4
    user_stats_testbed.expected_blocks_count += 1
    device_stats_testbed.expected_blocks_size += 4
    device_stats_testbed.expected_blocks_count += 1
    realm_stats_testbed.expected_blocks_size += 4
    realm_stats_testbed.expected_blocks_count += 1

    await generator.add_block(data_size=4, author=alice2, realm_id=realm)
    organization_stats_testbed.expected_blocks_size += 4
    organization_stats_testbed.expected_blocks_count += 1
    user_stats_testbed.expected_blocks_size += 4
    user_stats_testbed.expected_blocks_count += 1
    realm_stats_testbed.expected_blocks_size += 4
    realm_stats_testbed.expected_blocks_count += 1

    await generator.add_block(data_size=4, author=otheralice)

    await assert_stats()

    # 3 - Test stats on user/device

    # Device creation update the last connected, so we have to mock the time here.
    with freeze_time(f"{next_year}-01-02") as now:
        otherdevice = await generator.add_device(org=coolorg)
        organization_stats_testbed.expected_last_connected_on = now
        organization_stats_testbed.expected_last_connected_by = otherdevice.device_id
        organization_stats_testbed.expected_users_count += 1
        organization_stats_testbed.expected_devices_count += 1

    with freeze_time(f"{next_year}-01-03") as now:
        alice3 = await generator.add_device(org=coolorg, base_device_id=f"{alice.user_id}@dev3")
        organization_stats_testbed.expected_last_connected_on = now
        organization_stats_testbed.expected_last_connected_by = alice3.device_id
        organization_stats_testbed.expected_devices_count += 1
        user_stats_testbed.expected_last_connected_on = now
        user_stats_testbed.expected_last_connected_by = alice3.device_id
        user_stats_testbed.expected_devices_count += 1

    with freeze_time(f"{next_year}-01-04"):
        await generator.add_device(org=otherorg)

    await assert_stats()

    # 4 - Test stats on last connection

    with freeze_time(f"{next_year}-01-05") as now:
        await generator.update_last_connected(device=alice)
        organization_stats_testbed.expected_last_connected_on = now
        organization_stats_testbed.expected_last_connected_by = alice.device_id
        user_stats_testbed.expected_last_connected_on = now
        user_stats_testbed.expected_last_connected_by = alice.device_id
        device_stats_testbed.expected_last_connected_on = now

    with freeze_time(f"{next_year}-01-06") as now:
        await generator.update_last_connected(device=alice2)
        organization_stats_testbed.expected_last_connected_on = now
        organization_stats_testbed.expected_last_connected_by = alice2.device_id
        user_stats_testbed.expected_last_connected_on = now
        user_stats_testbed.expected_last_connected_by = alice2.device_id

    with freeze_time(f"{next_year}-01-07"):
        await generator.update_last_connected(device=otheralice)

    await assert_stats()
