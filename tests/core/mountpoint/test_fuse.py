# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
from unittest.mock import patch

import pytest
import trio

from parsec.core.core_events import CoreEvent
from parsec.core.mountpoint import MountpointDriverCrash, mountpoint_manager_factory


@pytest.mark.linux  # win32 doesn't allow to remove an opened file
@pytest.mark.mountpoint
def test_delete_then_close_file(mountpoint_service):
    async def _bootstrap(user_fs, mountpoint_manager):
        workspace = user_fs.get_workspace(mountpoint_service.wid)
        await workspace.touch("/with_fsync.txt")
        await workspace.touch("/without_fsync.txt")

    mountpoint_service.execute(_bootstrap)

    w_path = mountpoint_service.wpath

    path = w_path / "with_fsync.txt"
    fd = os.open(path, os.O_RDWR)
    os.unlink(path)
    os.fsync(fd)
    os.close(fd)

    path = w_path / "without_fsync.txt"
    fd = os.open(path, os.O_RDWR)
    os.unlink(path)
    os.close(fd)


@pytest.mark.linux
@pytest.mark.trio
@pytest.mark.mountpoint
async def test_unmount_with_fusermount(base_mountpoint, alice, alice_user_fs, event_bus):
    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    await workspace.touch("/bar.txt")

    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:

        with event_bus.listen() as spy:
            mountpoint_path = await mountpoint_manager.mount_workspace(wid)
            command = f"fusermount -u {mountpoint_path}".split()
            expected = {"mountpoint": mountpoint_path, "workspace_id": wid, "timestamp": None}

            completed_process = await trio.run_process(command)
            with trio.fail_after(1):
                # fusermount might fail for some reasons
                while completed_process.returncode:
                    completed_process = await trio.run_process(command)
                await spy.wait(CoreEvent.MOUNTPOINT_STOPPED, expected)

        assert not await trio.Path(mountpoint_path / "bar.txt").exists()

    # Mountpoint path should be removed on umounting
    assert not await trio.Path(mountpoint_path).exists()


@pytest.mark.linux
@pytest.mark.trio
@pytest.mark.mountpoint
async def test_hard_crash_in_fuse_thread(base_mountpoint, alice_user_fs):
    wid = await alice_user_fs.workspace_create("w")
    mountpoint_path = base_mountpoint / "w"

    class ToughLuckError(Exception):
        pass

    def _crash_fuse(*args, **kwargs):
        raise ToughLuckError("Tough luck...")

    with patch("parsec.core.mountpoint.fuse_runner.FUSE", new=_crash_fuse):
        async with mountpoint_manager_factory(
            alice_user_fs, alice_user_fs.event_bus, base_mountpoint
        ) as mountpoint_manager:

            with pytest.raises(MountpointDriverCrash) as exc:
                await mountpoint_manager.mount_workspace(wid)
            assert exc.value.args == (
                f"Fuse has crashed on {mountpoint_path}: Unknown error code: Tough luck...",
            )

    # Mountpoint path should be removed on umounting
    assert not await trio.Path(mountpoint_path).exists()


@pytest.mark.linux
@pytest.mark.trio
@pytest.mark.mountpoint
async def test_unmount_due_to_cancelled_scope(base_mountpoint, alice, alice_user_fs, event_bus):
    mountpoint_path = base_mountpoint / "w"
    wid = await alice_user_fs.workspace_create("w")

    with trio.CancelScope() as cancel_scope:
        async with mountpoint_manager_factory(
            alice_user_fs, event_bus, base_mountpoint
        ) as mountpoint_manager:

            await mountpoint_manager.mount_workspace(wid)
            cancel_scope.cancel()

    # Mountpoint path should be removed on umounting
    assert not await trio.Path(mountpoint_path).exists()


@pytest.mark.linux
@pytest.mark.trio
@pytest.mark.mountpoint
async def test_mountpoint_path_already_in_use_concurrent_with_non_empty_dir(
    monkeypatch, base_mountpoint, alice_user_fs
):
    wid = await alice_user_fs.workspace_create("w")
    mountpoint_path = base_mountpoint.absolute() / "w"

    # Here instead of checking the path can be used as a mountpoint, we
    # actually make it unsuitable to check the following behavior

    async def _mocked_bootstrap_mountpoint(*args):
        trio_mountpoint_path = trio.Path(f"{mountpoint_path}")
        await trio_mountpoint_path.mkdir(parents=True)
        file_path = trio_mountpoint_path / "bar.txt"
        await file_path.touch()
        st_dev = (await trio_mountpoint_path.stat()).st_dev
        return mountpoint_path, st_dev

    monkeypatch.setattr(
        "parsec.core.mountpoint.fuse_runner._bootstrap_mountpoint", _mocked_bootstrap_mountpoint
    )

    # Now we can start fuse
    async with mountpoint_manager_factory(
        alice_user_fs, alice_user_fs.event_bus, base_mountpoint
    ) as alice_mountpoint_manager:
        with pytest.raises(MountpointDriverCrash) as exc:
            await alice_mountpoint_manager.mount_workspace(wid)
        assert exc.value.args == (f"Fuse has crashed on {mountpoint_path}: EPERM",)


@pytest.mark.linux
@pytest.mark.trio
@pytest.mark.mountpoint
async def test_mountpoint_path_already_in_use_concurrent_with_mountpoint(
    monkeypatch, base_mountpoint, running_backend, alice_user_fs, alice2_user_fs
):
    # Create a workspace and make it available in two devices
    wid = await alice_user_fs.workspace_create("w")
    await alice_user_fs.sync()
    await alice2_user_fs.sync()

    mountpoint_path = base_mountpoint.absolute() / "w"

    async def _mount_alice2_w_mountpoint(*, task_status=trio.TASK_STATUS_IGNORED):
        async with mountpoint_manager_factory(
            alice2_user_fs, alice2_user_fs.event_bus, base_mountpoint
        ) as alice2_mountpoint_manager:
            await alice2_mountpoint_manager.mount_workspace(wid)
            task_status.started()
            await trio.sleep_forever()

    async with trio.open_service_nursery() as nursery:
        await nursery.start(_mount_alice2_w_mountpoint)

        # Here instead of checking the path can be used as a mountpoint, we
        # actually lead it into error

        async def _mocked_bootstrap_mountpoint(*args):
            trio_mountpoint_path = trio.Path(f"{mountpoint_path}")
            st_dev = (await trio_mountpoint_path.stat()).st_dev
            return mountpoint_path, st_dev

        monkeypatch.setattr(
            "parsec.core.mountpoint.fuse_runner._bootstrap_mountpoint", _mocked_bootstrap_mountpoint
        )

        # Now we can start fuse
        async with mountpoint_manager_factory(
            alice_user_fs, alice_user_fs.event_bus, base_mountpoint
        ) as alice_mountpoint_manager:
            with pytest.raises(MountpointDriverCrash) as exc:
                await alice_mountpoint_manager.mount_workspace(wid)
            assert exc.value.args == (f"Fuse has crashed on {mountpoint_path}: EPERM",)

        # Test is over, stop alice2 mountpoint and exit
        nursery.cancel_scope.cancel()
