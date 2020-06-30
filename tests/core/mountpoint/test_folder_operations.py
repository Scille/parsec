# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import errno
import os
from pathlib import Path
from string import ascii_lowercase

import attr
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    rule,
    run_state_machine_as_test,
)

# The point is not to find breaking filenames here, so keep it simple
st_entry_name = st.text(alphabet=ascii_lowercase, min_size=1, max_size=3)


class expect_raises:
    def __init__(self, expected_exc):
        self.expected_exc = expected_exc

    def __enter__(self):
        __tracebackhide__ = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        __tracebackhide__ = True

        if not self.expected_exc:
            return False

        if not exc_type:
            raise AssertionError(f"DID NOT RAISED {self.expected_exc!r}")

        # WinFSP error handling is not stricly similar to the real
        # Windows file system; so we often endup with the wrong exception
        # (e.g. `NotADirectoryError` when we expect `FileNotFoundError`)
        if os.name == "nt":
            allowed = OSError
        else:
            allowed = type(self.expected_exc)

        if not isinstance(exc_value, allowed):
            raise AssertionError(
                f"RAISED {exc_value!r} BUT EXPECTED {self.expected_exc!r}"
            ) from exc_value

        return True


def oracle_rename(src, dst):
    """The oracle must behave differently than `src.rename`, as the
    workspace file system does not support cross-directory renaming.
    """
    if src.parent != dst.parent:
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))
    return src.rename(str(dst))


@attr.s
class PathElement:
    absolute_path = attr.ib()
    parsec_root = attr.ib()
    oracle_root = attr.ib()

    def is_workspace(self):
        return len(Path(self.absolute_path).parts) == 1

    def to_oracle(self):
        return self.oracle_root / self.absolute_path[1:]

    def to_parsec(self):
        return self.parsec_root / self.absolute_path[1:]

    def __truediv__(self, path):
        assert isinstance(path, str) and path[0] != "/"
        absolute_path = f"/{path}" if self.absolute_path == "/" else f"{self.absolute_path}/{path}"
        return PathElement(absolute_path, self.parsec_root, self.oracle_root)


@pytest.mark.slow
@pytest.mark.mountpoint
@pytest.mark.flaky(reruns=1)
def test_folder_operations(tmpdir, caplog, hypothesis_settings, mountpoint_service_factory):

    tentative = 0

    class FolderOperationsStateMachine(RuleBasedStateMachine):
        Files = Bundle("file")
        Folders = Bundle("folder")
        # Moving mountpoint
        NonRootFolder = Folders.filter(lambda x: not x.is_workspace())

        @initialize(target=Folders)
        def init(self):
            nonlocal tentative
            tentative += 1
            caplog.clear()

            async def _bootstrap(user_fs, mountpoint_manager):
                wid = await user_fs.workspace_create("w")
                self.parsec_root = await mountpoint_manager.mount_workspace(wid)

            self.mountpoint_service = mountpoint_service_factory(_bootstrap)

            self.folder_oracle = Path(tmpdir / f"oracle-test-{tentative}")
            self.folder_oracle.mkdir()
            oracle_root = self.folder_oracle / "root"
            oracle_root.mkdir()
            self.folder_oracle.chmod(0o500)  # Root oracle can no longer be removed this way
            (oracle_root / "w").mkdir()
            oracle_root.chmod(0o500)  # Also protect workspace from deletion

            return PathElement(f"/", self.parsec_root, oracle_root / "w")

        def teardown(self):
            if hasattr(self, "mountpoint_service"):
                self.mountpoint_service.stop()

        @rule(target=Files, parent=Folders, name=st_entry_name)
        def touch(self, parent, name):
            path = parent / name

            expected_exc = None
            try:
                path.to_oracle().touch(exist_ok=False)
            except OSError as exc:
                expected_exc = exc

            with expect_raises(expected_exc):
                path.to_parsec().touch(exist_ok=False)

            return path

        @rule(target=Folders, parent=Folders, name=st_entry_name)
        def mkdir(self, parent, name):
            path = parent / name

            expected_exc = None
            try:
                path.to_oracle().mkdir(exist_ok=False)
            except OSError as exc:
                expected_exc = exc

            with expect_raises(expected_exc):
                path.to_parsec().mkdir(exist_ok=False)

            return path

        @rule(path=Files)
        def unlink(self, path):
            expected_exc = None
            try:
                path.to_oracle().unlink()
            except OSError as exc:
                expected_exc = exc

            with expect_raises(expected_exc):
                path.to_parsec().unlink()

        @rule(path=Files, length=st.integers(min_value=0, max_value=16))
        def resize(self, path, length):
            expected_exc = None
            try:
                os.truncate(path.to_oracle(), length)
            except OSError as exc:
                expected_exc = exc

            with expect_raises(expected_exc):
                os.truncate(path.to_parsec(), length)

        @rule(path=NonRootFolder)
        def rmdir(self, path):
            expected_exc = None
            try:
                path.to_oracle().rmdir()
            except OSError as exc:
                expected_exc = exc

            with expect_raises(expected_exc):
                path.to_parsec().rmdir()

        def _move(self, src, dst_parent, dst_name):
            dst = dst_parent / dst_name

            expected_exc = None
            try:
                oracle_rename(src.to_oracle(), dst.to_oracle())
            except OSError as exc:
                expected_exc = exc

            with expect_raises(expected_exc):
                src.to_parsec().rename(str(dst.to_parsec()))

            return dst

        @rule(target=Files, src=Files, dst_parent=Folders, dst_name=st_entry_name)
        def move_file(self, src, dst_parent, dst_name):
            return self._move(src, dst_parent, dst_name)

        @rule(target=Folders, src=NonRootFolder, dst_parent=Folders, dst_name=st_entry_name)
        def move_folder(self, src, dst_parent, dst_name):
            return self._move(src, dst_parent, dst_name)

        @rule(path=Folders)
        def iterdir(self, path):
            expected_exc = None
            try:
                expected_children = {x.name for x in path.to_oracle().iterdir()}
            except OSError as exc:
                expected_exc = exc

            with expect_raises(expected_exc):
                children = {x.name for x in path.to_parsec().iterdir()}

            if not expected_exc:
                assert children == expected_children

    run_state_machine_as_test(FolderOperationsStateMachine, settings=hypothesis_settings)
