# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import errno
from uuid import uuid4

import trio
import pytest
from pathlib import Path, PurePath

from parsec.core.mountpoint import (
    mountpoint_manager_factory,
    MountpointDisabled,
    MountpointConfigurationError,
    MountpointAlreadyMounted,
    MountpointNotMounted,
)
from parsec.core import logged_core_factory
from parsec.core.types import FsPath, WorkspaceRole

from tests.common import create_shared_workspace


@pytest.mark.trio
async def test_runner_not_available(monkeypatch, alice_user_fs, event_bus):
    base_mountpoint = Path("/foo")

    monkeypatch.setattr("parsec.core.mountpoint.manager.get_mountpoint_runner", lambda: None)
    with pytest.raises(RuntimeError):
        async with mountpoint_manager_factory(alice_user_fs, event_bus, base_mountpoint):
            pass


@pytest.mark.trio
async def test_mountpoint_disabled(monkeypatch, alice_user_fs, event_bus):
    base_mountpoint = Path("/foo")

    wid = await alice_user_fs.workspace_create("/w")

    monkeypatch.setattr("parsec.core.mountpoint.manager.get_mountpoint_runner", lambda: None)
    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint, enabled=False
    ) as mountpoint_manager:
        with pytest.raises(MountpointDisabled):
            await mountpoint_manager.mount_workspace(wid)


@pytest.mark.trio
async def test_mount_unknown_workspace(base_mountpoint, alice_user_fs, event_bus):
    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:
        wid = uuid4()
        with pytest.raises(MountpointConfigurationError) as exc:
            await mountpoint_manager.mount_workspace(wid)

        assert exc.value.args == (f"Workspace `{wid}` doesn't exist",)


@pytest.mark.trio
@pytest.mark.mountpoint
async def test_base_mountpoint_not_created(base_mountpoint, alice_user_fs, event_bus):
    # Path should be created if it doesn' exist
    base_mountpoint = base_mountpoint / "dummy/dummy/dummy"
    mountpoint = f"{base_mountpoint.absolute()}/w"

    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    await workspace.touch("/bar.txt")

    bar_txt = trio.Path(f"{mountpoint}/bar.txt")

    # Now we can start fuse

    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:

        await mountpoint_manager.mount_workspace(wid)
        assert await bar_txt.exists()


@pytest.mark.trio
@pytest.mark.mountpoint
async def test_mountpoint_path_already_in_use(
    base_mountpoint, running_backend, alice_user_fs, alice2_user_fs
):
    # Create a workspace and make it available in two devices
    wid = await alice_user_fs.workspace_create("w")
    await alice_user_fs.sync()
    await alice2_user_fs.sync()

    # Easily differenciate alice&alice2
    await alice2_user_fs.get_workspace(wid).touch("/I_am_alice2.txt")
    await alice_user_fs.get_workspace(wid).touch("/I_am_alice.txt")

    naive_workspace_path = (base_mountpoint / "w").absolute()

    # Default workspace path already exists, souldn't be able to use it
    await trio.Path(base_mountpoint / "w").mkdir(parents=True)
    await trio.Path(base_mountpoint / "w" / "bar.txt").touch()

    async with mountpoint_manager_factory(
        alice_user_fs, alice_user_fs.event_bus, base_mountpoint
    ) as alice_mountpoint_manager, mountpoint_manager_factory(
        alice2_user_fs, alice2_user_fs.event_bus, base_mountpoint
    ) as alice2_mountpoint_manager:
        # Alice mount the workspace first
        alice_mountpoint_path = await alice_mountpoint_manager.mount_workspace(wid)
        assert str(alice_mountpoint_path) == f"{naive_workspace_path} (2)"

        # Alice2 should also be able to mount the workspace without name clashing
        alice2_mountpoint_path = await alice2_mountpoint_manager.mount_workspace(wid)
        assert str(alice2_mountpoint_path) == f"{naive_workspace_path} (3)"

        # Finally make sure each workspace is well mounted
        assert await trio.Path(alice_mountpoint_path / "I_am_alice.txt").exists()
        assert await trio.Path(alice2_mountpoint_path / "I_am_alice2.txt").exists()


@pytest.mark.trio
@pytest.mark.mountpoint
@pytest.mark.parametrize("manual_unmount", [True, False])
async def test_mount_and_explore_workspace(
    base_mountpoint, alice_user_fs, event_bus, manual_unmount
):
    # Populate a bit the fs first...

    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    await workspace.mkdir("/foo")
    await workspace.touch("/bar.txt")
    await workspace.write_bytes("/bar.txt", b"Hello world !")

    # Now we can start fuse

    with event_bus.listen() as spy:

        async with mountpoint_manager_factory(
            alice_user_fs, event_bus, base_mountpoint
        ) as mountpoint_manager:

            await mountpoint_manager.mount_workspace(wid)
            mountpoint_path = base_mountpoint / "w"

            spy.assert_events_occured(
                [
                    ("mountpoint.starting", {"mountpoint": mountpoint_path}),
                    ("mountpoint.started", {"mountpoint": mountpoint_path}),
                ]
            )

            # Finally explore the mountpoint

            def inspect_mountpoint():
                wksp_children = set(os.listdir(mountpoint_path))
                assert wksp_children == {"foo", "bar.txt"}

                bar_stat = os.stat(f"{mountpoint_path}/bar.txt")
                assert bar_stat.st_size == len(b"Hello world !")

                with open(f"{mountpoint_path}/bar.txt", "rb") as fd:
                    bar_txt = fd.read()
                assert bar_txt == b"Hello world !"

            # Note given python fs api is blocking, we must run it inside a thread
            # to avoid blocking the trio loop and ending up in a deadlock
            await trio.run_sync_in_worker_thread(inspect_mountpoint)

            if manual_unmount:
                await mountpoint_manager.unmount_workspace(wid)
                # Mountpoint should be stopped by now
                spy.assert_events_occured([("mountpoint.stopped", {"mountpoint": mountpoint_path})])

        if not manual_unmount:
            # Mountpoint should be stopped by now
            spy.assert_events_occured([("mountpoint.stopped", {"mountpoint": mountpoint_path})])


@pytest.mark.trio
@pytest.mark.mountpoint
@pytest.mark.parametrize("manual_unmount", [True, False])
async def test_idempotent_mount(base_mountpoint, alice_user_fs, event_bus, manual_unmount):
    mountpoint_path = base_mountpoint / "w"

    # Populate a bit the fs first...

    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    await workspace.touch("/bar.txt")

    bar_txt = trio.Path(f"{mountpoint_path}/bar.txt")

    # Now we can start fuse

    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:

        await mountpoint_manager.mount_workspace(wid)
        assert await bar_txt.exists()

        with pytest.raises(MountpointAlreadyMounted):
            await mountpoint_manager.mount_workspace(wid)
        assert await bar_txt.exists()

        await mountpoint_manager.unmount_workspace(wid)
        assert not await bar_txt.exists()

        with pytest.raises(MountpointNotMounted):
            await mountpoint_manager.unmount_workspace(wid)
        assert not await bar_txt.exists()

        await mountpoint_manager.mount_workspace(wid)
        assert await bar_txt.exists()


@pytest.mark.trio
@pytest.mark.mountpoint
async def test_work_within_logged_core(base_mountpoint, core_config, alice, tmpdir):
    core_config = core_config.evolve(mountpoint_enabled=True, mountpoint_base_dir=base_mountpoint)
    mountpoint_path = base_mountpoint / "w"
    bar_txt = trio.Path(f"{mountpoint_path}/bar.txt")

    async with logged_core_factory(core_config, alice) as alice_core:
        wid = await alice_core.user_fs.workspace_create("w")
        workspace = alice_core.user_fs.get_workspace(wid)
        await workspace.touch("/bar.txt")

        assert not await bar_txt.exists()

        await alice_core.mountpoint_manager.mount_workspace(wid)

        assert await bar_txt.exists()

    assert not await bar_txt.exists()


@pytest.mark.mountpoint
def test_manifest_not_available(mountpoint_service):
    async def _bootstrap(user_fs, mountpoint_manager):
        wid = await user_fs.workspace_create("x")
        workspace = user_fs.get_workspace(wid)
        await workspace.touch("/foo.txt")
        foo_id = await workspace.path_id("/foo.txt")
        async with workspace.local_storage.lock_entry_id(foo_id):
            await workspace.local_storage.clear_manifest(foo_id)
        await mountpoint_manager.mount_all()

    mountpoint_service.start()
    mountpoint_service.execute(_bootstrap)
    x_path = mountpoint_service.get_workspace_mountpoint("x")

    with pytest.raises(OSError) as exc:
        (x_path / "foo.txt").stat()
    if os.name == "nt":
        # This winerror code corresponds to ntstatus.STATUS_HOST_UNREACHABLE
        ERROR_HOST_UNREACHABLE = 1232
        assert exc.value.winerror == ERROR_HOST_UNREACHABLE
    else:
        assert exc.value.errno == errno.EHOSTUNREACH


@pytest.mark.trio
@pytest.mark.mountpoint
async def test_get_path_in_mountpoint(base_mountpoint, alice_user_fs, event_bus):
    # Populate a bit the fs first...
    wid = await alice_user_fs.workspace_create("mounted_wksp")
    wid2 = await alice_user_fs.workspace_create("not_mounted_wksp")
    workspace1 = alice_user_fs.get_workspace(wid)
    workspace2 = alice_user_fs.get_workspace(wid2)
    await workspace1.touch("/bar.txt")
    await workspace2.touch("/foo.txt")

    # Now we can start fuse
    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:
        await mountpoint_manager.mount_workspace(wid)

        bar_path = mountpoint_manager.get_path_in_mountpoint(wid, FsPath("/bar.txt"))

        assert isinstance(bar_path, PurePath)
        expected = base_mountpoint / f"mounted_wksp" / "bar.txt"
        assert str(bar_path) == str(expected.absolute())
        assert await trio.Path(bar_path).exists()

        with pytest.raises(MountpointNotMounted):
            mountpoint_manager.get_path_in_mountpoint(wid2, FsPath("/foo.txt"))


@pytest.mark.mountpoint
def test_unhandled_crash_in_fs_operation(caplog, mountpoint_service, monkeypatch):
    from parsec.core.mountpoint.thread_fs_access import ThreadFSAccess

    vanilla_entry_info = ThreadFSAccess.entry_info

    def _entry_info_crash(self, path):
        if str(path) == "/crash_me":
            raise RuntimeError("Crashed !")
        else:
            return vanilla_entry_info(self, path)

    monkeypatch.setattr(
        "parsec.core.mountpoint.thread_fs_access.ThreadFSAccess.entry_info", _entry_info_crash
    )

    mountpoint_service.start()
    mountpoint = mountpoint_service.get_default_workspace_mountpoint()
    with pytest.raises(OSError) as exc:
        (mountpoint / "crash_me").stat()

    if os.name == "nt":
        assert exc.value.args == (22, "An internal error occurred")
        caplog.assert_occured(
            "[exception] Unhandled exception in winfsp mountpoint [parsec.core.mountpoint.winfsp_operations]"
        )

    else:
        assert exc.value.args == (5, "Input/output error")
        caplog.assert_occured(
            "[exception] Unhandled exception in fuse mountpoint [parsec.core.mountpoint.fuse_operations]"
        )


@pytest.mark.trio
@pytest.mark.mountpoint
@pytest.mark.parametrize("revoking", ["read", "write"])
async def test_mountpoint_revoke_access(
    base_mountpoint,
    alice_user_fs,
    alice2_user_fs,
    bob_user_fs,
    event_bus,
    running_backend,
    revoking,
):
    # Parametrization
    new_role = None if revoking == "read" else WorkspaceRole.READER

    # Bob creates and share two files with Alice
    wid = await create_shared_workspace("w", bob_user_fs, alice_user_fs, alice2_user_fs)
    workspace = bob_user_fs.get_workspace(wid)
    await workspace.touch("/foo.txt")
    await workspace.touch("/bar.txt")
    await workspace.sync()

    def get_root_path(mountpoint_manager):
        root_path = mountpoint_manager.get_path_in_mountpoint(wid, FsPath("/"))
        # A trio path is required here, otherwise we risk a messy deadlock!
        return trio.Path(root_path)

    async def assert_cannot_read(mountpoint_manager, root_is_cached=False):
        root_path = get_root_path(mountpoint_manager)
        foo_path = root_path / "foo.txt"
        bar_path = root_path / "bar.txt"
        # For some reason, root_path.stat() does not trigger a new getattr call
        # to fuse operations if there has been a prior recent call to stat.
        if not root_is_cached:
            with pytest.raises(PermissionError):
                await root_path.stat()
        with pytest.raises(PermissionError):
            await foo_path.exists()
        with pytest.raises(PermissionError):
            await foo_path.read_bytes()
        with pytest.raises(PermissionError):
            await bar_path.exists()
        with pytest.raises(PermissionError):
            await bar_path.read_bytes()

    async def assert_cannot_write(mountpoint_manager):
        root_path = get_root_path(mountpoint_manager)
        foo_path = root_path / "foo.txt"
        bar_path = root_path / "bar.txt"
        with pytest.raises(PermissionError):
            await (root_path / "new_file.txt").touch()
        with pytest.raises(PermissionError):
            await (root_path / "new_directory").mkdir()
        with pytest.raises(PermissionError):
            await foo_path.write_bytes(b"foo contents")
        with pytest.raises(PermissionError):
            await foo_path.unlink()
        with pytest.raises(PermissionError):
            await bar_path.write_bytes(b"bar contents")
        with pytest.raises(PermissionError):
            await bar_path.unlink()

    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:
        # Mount Bob workspace on Alice's side
        await mountpoint_manager.mount_workspace(wid)
        root_path = get_root_path(mountpoint_manager)

        # Alice can read
        await (root_path / "bar.txt").read_bytes()

        # Alice can write
        await (root_path / "bar.txt").write_bytes(b"test")

        # Bob revokes Alice's read or write rights from her workspace
        await bob_user_fs.workspace_share(wid, alice_user_fs.device.user_id, new_role)

        # Let Alice process the info
        await alice_user_fs.process_last_messages()
        await alice2_user_fs.process_last_messages()

        # Alice still has read access
        if new_role is WorkspaceRole.READER:
            await (root_path / "bar.txt").read_bytes()

        # Alice no longer has read access
        else:
            await assert_cannot_read(mountpoint_manager, root_is_cached=True)

        # Alice no longer has write access
        await assert_cannot_write(mountpoint_manager)

    # Try again with Alice first device

    async with mountpoint_manager_factory(
        alice_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:
        # Mount alice workspace on bob's side once again
        await mountpoint_manager.mount_workspace(wid)
        root_path = get_root_path(mountpoint_manager)

        # Alice still has read access
        if new_role is WorkspaceRole.READER:
            await (root_path / "bar.txt").read_bytes()

        # Alice no longer has read access
        else:
            await assert_cannot_read(mountpoint_manager, root_is_cached=True)

        # Alice no longer has write access
        await assert_cannot_write(mountpoint_manager)

    # Try again with Alice second device

    async with mountpoint_manager_factory(
        alice2_user_fs, event_bus, base_mountpoint
    ) as mountpoint_manager:
        # Mount alice workspace on bob's side once again
        await mountpoint_manager.mount_workspace(wid)
        root_path = get_root_path(mountpoint_manager)

        # Alice still has read access
        if new_role is WorkspaceRole.READER:
            await (root_path / "bar.txt").read_bytes()

        # Alice no longer has read access
        else:
            await assert_cannot_read(mountpoint_manager, root_is_cached=True)

        # Alice no longer has write access
        await assert_cannot_write(mountpoint_manager)
