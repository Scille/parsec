# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pytest
from pendulum import Pendulum

from parsec.core.core_events import CoreEvent
from tests.common import freeze_time


@pytest.mark.trio
@pytest.mark.backend_not_populated
@pytest.mark.skip  #  TODO refactoring?
async def test_lazy_root_manifest_generation(
    running_backend, backend_data_binder, local_storage_factory, user_fs_factory, coolorg, alice
):
    with freeze_time("2000-01-01"):
        await backend_data_binder.bind_organization(
            coolorg, alice, initial_user_manifest_in_v0=True
        )
    local_storage = await local_storage_factory(alice, user_manifest_in_v0=True)

    async with user_fs_factory(alice, local_storage) as user_fs:
        wid = await user_fs.workspace_create("w")
        workspace = user_fs.get_workspace(wid)
        with freeze_time("2000-01-02"):
            stat = await workspace.path_info("/")

        assert stat == {
            "type": "root",
            "id": alice.user_manifest_id,
            "created": Pendulum(2000, 1, 2),
            "updated": Pendulum(2000, 1, 2),
            "base_version": 0,
            "is_folder": True,
            "is_placeholder": True,
            "need_sync": True,
            "children": [],
        }

        with freeze_time("2000-01-03"):
            await workspace.sync()

        stat = await workspace.path_info("/")
        assert stat == {
            "type": "root",
            "id": ANY,
            "created": Pendulum(2000, 1, 2),
            "updated": Pendulum(2000, 1, 2),
            "base_version": 1,
            "is_folder": True,
            "is_placeholder": False,
            "need_sync": False,
            "children": [],
        }


@pytest.mark.trio
@pytest.mark.backend_not_populated
@pytest.mark.xfail
async def test_concurrent_devices_agreed_on_root_manifest(
    running_backend,
    backend_data_binder,
    local_storage_factory,
    user_fs_factory,
    coolorg,
    alice,
    alice2,
):
    with freeze_time("2000-01-01"):
        await backend_data_binder.bind_organization(
            coolorg, alice, initial_user_manifest_in_v0=True
        )
        await backend_data_binder.bind_device(alice2, initial_user_manifest_in_v0=True)

    alice_local_storage = await local_storage_factory(alice, user_manifest_in_v0=True)
    alice2_local_storage = await local_storage_factory(alice2, user_manifest_in_v0=True)

    async with user_fs_factory(alice, alice_local_storage) as user_fs1, user_fs_factory(
        alice2, alice2_local_storage
    ) as user_fs2:

        with freeze_time("2000-01-03"):
            wid1 = await user_fs1.workspace_create("from_1")
            workspace1 = user_fs1.get_workspace(wid1)
        with freeze_time("2000-01-04"):
            wid2 = await user_fs2.workspace_create("from_2")
            workspace2 = user_fs2.get_workspace(wid2)

        with user_fs1.event_bus.listen() as spy:
            with freeze_time("2000-01-05"):
                await user_fs1.sync()
        date_sync = Pendulum(2000, 1, 5)
        spy.assert_events_exactly_occured(
            [
                (CoreEvent.FS_ENTRY_MINIMAL_SYNCED, {"id": alice.user_manifest_id}, date_sync),
                (CoreEvent.FS_ENTRY_MINIMAL_SYNCED, {"id": wid1}, date_sync),
                (CoreEvent.FS_ENTRY_SYNCED, {"id": alice.user_manifest_id}, date_sync),
                (CoreEvent.FS_ENTRY_SYNCED, {"id": wid1}, date_sync),
            ]
        )

        with user_fs2.event_bus.listen() as spy:
            with freeze_time("2000-01-06"):
                await user_fs2.sync()
        date_sync = Pendulum(2000, 1, 6)
        spy.assert_events_exactly_occured(
            [
                (CoreEvent.FS_ENTRY_MINIMAL_SYNCED, {"id": alice.user_manifest_id}, date_sync),
                (CoreEvent.FS_ENTRY_MINIMAL_SYNCED, {"id": wid2}, date_sync),
                (CoreEvent.FS_ENTRY_SYNCED, {"id": alice.user_manifest_id}, date_sync),
                (CoreEvent.FS_ENTRY_SYNCED, {"id": wid2}, date_sync),
            ]
        )

        with user_fs1.event_bus.listen() as spy:
            with freeze_time("2000-01-07"):
                await user_fs1.sync()
        date_sync = Pendulum(2000, 1, 7)
        spy.assert_events_exactly_occured(
            [
                (
                    CoreEvent.FS_ENTRY_REMOTE_CHANGED,
                    {"path": "/", "id": spy.ANY},
                    date_sync,
                )  # TODO: really needed ?
            ]
        )

    path_info1 = await workspace1.path_info("/")
    path_info2 = await workspace2.path_info("/")
    assert path_info1 == {
        "type": "root",
        "id": alice.user_manifest_id,
        "created": Pendulum(2000, 1, 3),
        "updated": Pendulum(2000, 1, 4),
        "base_version": 3,
        "is_folder": True,
        "is_placeholder": False,
        "need_sync": False,
        "children": ["from_1", "from_2"],
    }
    assert path_info1 == path_info2


@pytest.mark.trio
@pytest.mark.backend_not_populated
@pytest.mark.skip  #  TODO refactoring?
async def test_reloading_v0_user_manifest(
    running_backend, backend_data_binder, local_storage_factory, user_fs_factory, coolorg, alice
):
    # Initialize backend and local storage
    with freeze_time("2000-01-01"):
        await backend_data_binder.bind_organization(
            coolorg, alice, initial_user_manifest_in_v0=True
        )
    local_storage = await local_storage_factory(alice, user_manifest_in_v0=True)

    # Create a workspace without syncronizing
    async with user_fs_factory(alice, local_storage) as user_fs:
        with freeze_time("2000-01-02"):
            wid = await user_fs.workspace_create("foo")
            workspace = user_fs.get_workspace(wid)

    await local_storage.clear_memory_cache()

    # Reload version 0 manifest
    async with user_fs_factory(alice, local_storage) as user_fs:
        with freeze_time("2000-01-02"):
            path_info = await workspace.path_info("/")

        assert path_info == {
            "type": "root",
            "id": alice.user_manifest_id,
            "created": Pendulum(2000, 1, 2),
            "updated": Pendulum(2000, 1, 2),
            "base_version": 0,
            "is_folder": True,
            "is_placeholder": True,
            "need_sync": True,
            "children": ["foo"],
        }

    await local_storage.clear_memory_cache()

    # Syncronize version 0 manifest
    async with user_fs_factory(alice, local_storage) as user_fs:
        with freeze_time("2000-01-03"):
            await user_fs.sync()

        path_info = await workspace.path_info("/")
        assert path_info == {
            "type": "root",
            "id": ANY,
            "created": Pendulum(2000, 1, 2),
            "updated": Pendulum(2000, 1, 2),
            "base_version": 1,
            "is_folder": True,
            "is_placeholder": False,
            "need_sync": False,
            "children": ["foo"],
        }
