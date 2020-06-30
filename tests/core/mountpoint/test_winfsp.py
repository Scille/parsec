# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import threading
import time

import pytest

from parsec.core.fs.utils import ntstatus


@pytest.mark.win32
@pytest.mark.mountpoint
def test_rename_to_another_drive(mountpoint_service):
    x_path = None
    y_path = None

    async def _bootstrap(user_fs, mountpoint_manager):
        nonlocal x_path, y_path
        xid = await user_fs.workspace_create("x")
        xworkspace = user_fs.get_workspace(xid)
        await xworkspace.touch("/foo.txt")
        yid = await user_fs.workspace_create("y")
        x_path = await mountpoint_manager.mount_workspace(xid)
        y_path = await mountpoint_manager.mount_workspace(yid)
        print(x_path, y_path)

    mountpoint_service.execute(_bootstrap)

    with pytest.raises(OSError) as exc:
        (x_path / "foo.txt").rename(y_path / "foo.txt")
    assert str(exc.value).startswith(
        "[WinError 17] The system cannot move the file to a different disk drive"
    )


@pytest.mark.win32
@pytest.mark.mountpoint
def test_teardown_during_fs_access(mountpoint_service, monkeypatch):
    mountpoint_needed_stop = threading.Event()
    mountpoint_winfsp_stop = threading.Event()
    mountpoint_stopped = threading.Event()

    from parsec.core.mountpoint.thread_fs_access import ThreadFSAccess
    from winfspy import FileSystem

    # Monkeypatch to trigger mountpoint teardown during an operation

    vanilla_entry_info = ThreadFSAccess.entry_info

    def _entry_info_maybe_stop_loop(self, path):
        if str(path) == "/stop_loop":
            mountpoint_needed_stop.set()
            # WinFSP's stop waits for the current operations (so including this
            # function !) to finish before returning.
            # Given this is the behavior we are testing here, make sure we
            # have actually reach this point before going further.
            mountpoint_winfsp_stop.wait()
            # Make sure winfsp shutdown gets blocked by this operation
            time.sleep(0.1)
        return vanilla_entry_info(self, path)

    monkeypatch.setattr(
        "parsec.core.mountpoint.thread_fs_access.ThreadFSAccess.entry_info",
        _entry_info_maybe_stop_loop,
    )

    # Monkeypatch WinFSP's stop to know when we are about to wait for the
    # current operations to finish

    vanilla_file_system_stop = FileSystem.stop

    def _patched_file_system_stop(self):
        mountpoint_winfsp_stop.set()
        return vanilla_file_system_stop(self)

    monkeypatch.setattr("winfspy.FileSystem.stop", _patched_file_system_stop)

    # Spawn a thread responsible for the mountpoint teardown when needed

    def _wait_and_stop_mountpoint():
        mountpoint_needed_stop.wait()
        try:
            mountpoint_service.stop()
        finally:
            mountpoint_stopped.set()

    thread = threading.Thread(target=_wait_and_stop_mountpoint)
    thread.setName("MountpointService")
    thread.start()

    # All boilerplates are set, let's do the actual test !

    try:

        with pytest.raises(OSError) as exc:
            (mountpoint_service.wpath / "stop_loop").stat()

        assert str(exc.value).startswith(
            "[WinError 995] The I/O operation has been aborted because of either a thread exit or an application request"
        )

    finally:
        mountpoint_needed_stop.set()
        mountpoint_stopped.wait()
        thread.join()


@pytest.mark.win32
@pytest.mark.mountpoint
def test_mount_workspace_with_non_win32_friendly_name(mountpoint_service_factory):
    # see https://docs.microsoft.com/en-us/windows/win32/fileio/naming-a-file
    items = (
        (
            # Invalid chars (except `/` which is not allowed in Parsec)
            '<>:"\\|?*',
            "~3c~3e~3a~22~5c~7c~3f~2a",
        ),
        (
            # Invalid control characters (1/2)
            "".join(chr(x) for x in range(1, 16)),
            "".join(f"~{x:02x}" for x in range(1, 16)),
        ),
        (
            # Invalid control characters (2/2)
            "".join(chr(x) for x in range(16, 32)),
            "".join(f"~{x:02x}" for x in range(16, 32)),
        ),
        (
            # Trailing dot not allowed
            "foo.",
            "foo~2e",
        ),
        (
            # Trailing space not allowed
            "foo ",
            "foo~20",
        ),
        (
            # Invalid name
            "CON",
            "CO~4e",
        ),
        (
            # Invalid name with extension
            "COM1.foo",
            "COM~31.foo",
        ),
    )

    async def _bootstrap(user_fs, mountpoint_manager):

        for name, _ in items:
            # Apply bad name to both the mountpoint folder and data inside it
            wid = await user_fs.workspace_create(name)
            workspace = user_fs.get_workspace(wid)
            await workspace.touch(f"/{name}")
            workspaces.append(await mountpoint_manager.mount_workspace(wid))
        # await mountpoint_manager.mount_all()

    workspaces = []
    mountpoint_service_factory(_bootstrap)

    for workspace, (_, cooked_name) in zip(workspaces, items):
        assert workspace.exists()
        entries = list(workspace.iterdir())
        assert [x.name for x in entries] == [cooked_name]
        assert entries[0].exists()


@pytest.mark.win32
@pytest.mark.mountpoint
def test_mount_workspace_with_too_long_name(mountpoint_service_factory):
    # WinFSP volume_label (with no trailing '\0') is stored on 32 WCHAR
    too_long = "x" * 33
    # Smiley takes 4 bytes (2 WCHAR) once encoded in UTF-16
    too_long_once_encoded = "x" + "😀" * 16

    async def _bootstrap(user_fs, mountpoint_manager):
        wid = await user_fs.workspace_create(too_long)
        workspaces.append(await mountpoint_manager.mount_workspace(wid))

        wid = await user_fs.workspace_create(too_long_once_encoded)
        workspaces.append(await mountpoint_manager.mount_workspace(wid))

    workspaces = []
    mountpoint_service_factory(_bootstrap)
    for workspace in workspaces:
        assert workspace.exists()


@pytest.mark.win32
@pytest.mark.mountpoint
def test_iterdir_with_marker(mountpoint_service):
    expected_entries_names = []

    async def _bootstrap(user_fs, mountpoint_manager):
        workspace = user_fs.get_workspace(mountpoint_service.wid)
        for i in range(150):
            if i < 50:
                # File name < `..` (`..` is always the first item in our implementation)
                path = f"/.-{i}"
            else:
                path = f"/{i}"
            if i % 2:
                await workspace.touch(path)
            else:
                await workspace.mkdir(path)
            expected_entries_names.append(path[1:])

    mountpoint_service.execute(_bootstrap)
    expected_entries_names = sorted(expected_entries_names)

    # Note `os.listdir()` ignores `.` and `..` entries
    entries_names = os.listdir(mountpoint_service.wpath)
    assert entries_names == expected_entries_names


@pytest.mark.win32
@pytest.mark.mountpoint
def test_ntstatus_in_fs_errors():
    from winfspy.plumbing.winstuff import NTSTATUS

    for status in ntstatus:
        assert getattr(NTSTATUS, status.name) == status


@pytest.mark.win32
@pytest.mark.mountpoint
def test_replace_if_exists(mountpoint_service):
    async def _bootstrap(user_fs, mountpoint_manager):
        workspace = user_fs.get_workspace(mountpoint_service.wid)
        await workspace.touch("/foo.txt")
        await workspace.touch("/bar.txt")
        await workspace.write_bytes("/foo.txt", b"foo")
        await workspace.write_bytes("/bar.txt", b"bar")

    mountpoint_service.execute(_bootstrap)

    foo = mountpoint_service.wpath / "foo.txt"
    bar = mountpoint_service.wpath / "bar.txt"
    assert foo.read_bytes() == b"foo"
    assert bar.read_bytes() == b"bar"
    foo.replace(bar)
    assert bar.read_bytes() == b"foo"
    assert not foo.exists()
