# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
from string import ascii_lowercase

import pytest
from hypothesis import strategies as st
from hypothesis_trio.stateful import Bundle, initialize, rule


def get_path(path):
    return path[2:] if path[2:] else "/"


# The point is not to find breaking filenames here, so keep it simple
st_entry_name = st.text(alphabet=ascii_lowercase, min_size=1, max_size=3)


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="Windows path style not compatible with oracle")
def test_fs_offline_restart_and_tree(user_fs_offline_state_machine, oracle_fs_factory, alice):
    class FSOfflineRestartAndTree(user_fs_offline_state_machine):
        Files = Bundle("file")
        Folders = Bundle("folder")

        @initialize(target=Folders)
        async def init(self):
            await self.reset_all()
            self.device = alice
            await self.restart_user_fs(self.device)
            self.wid = await self.user_fs.workspace_create("w")
            self.workspace = self.user_fs.get_workspace(self.wid)

            self.oracle_fs = oracle_fs_factory()
            self.oracle_fs.create_workspace("/w")
            return "/w"

        @rule()
        async def restart(self):
            await self.restart_user_fs(self.device)
            self.workspace = self.user_fs.get_workspace(self.wid)

        @rule(target=Files, parent=Folders, name=st_entry_name)
        async def create_file(self, parent, name):
            path = f"{parent}/{name}"
            expected_status = self.oracle_fs.create_file(path)
            if expected_status == "ok":
                await self.workspace.touch(path=get_path(path), exist_ok=False)
            else:
                with pytest.raises((FileExistsError, FileNotFoundError, NotADirectoryError)):
                    await self.workspace.touch(path=get_path(path), exist_ok=False)
            return path

        @rule(target=Folders, parent=Folders, name=st_entry_name)
        async def create_folder(self, parent, name):
            path = f"{parent}/{name}"
            expected_status = self.oracle_fs.create_folder(path)
            if expected_status == "ok":
                await self.workspace.mkdir(path=get_path(path), exist_ok=False)
            else:
                with pytest.raises((FileExistsError, FileNotFoundError, NotADirectoryError)):
                    await self.workspace.mkdir(path=get_path(path), exist_ok=False)
            return path

        @rule(path=Files)
        async def delete_file(self, path):
            expected_status = self.oracle_fs.unlink(path)
            if expected_status == "ok":
                await self.workspace.unlink(path=get_path(path))
            else:
                with pytest.raises(OSError):
                    await self.workspace.unlink(path=get_path(path))
            return path

        @rule(path=Folders)
        async def delete_folder(self, path):
            expected_status = self.oracle_fs.rmdir(path)
            if expected_status == "ok":
                await self.workspace.rmdir(path=get_path(path))
            else:
                with pytest.raises(OSError):
                    await self.workspace.rmdir(path=get_path(path))
            return path

        @rule(target=Files, src=Files, dst_parent=Folders, dst_name=st_entry_name)
        async def move_file(self, src, dst_parent, dst_name):
            dst = f"{dst_parent}/{dst_name}"
            expected_status = self.oracle_fs.move(src, dst)
            if expected_status == "ok":
                await self.workspace.rename(get_path(src), get_path(dst))
            else:
                with pytest.raises(OSError):
                    await self.workspace.rename(get_path(src), get_path(dst))
            return dst

        @rule(target=Folders, src=Folders, dst_parent=Folders, dst_name=st_entry_name)
        async def move_folder(self, src, dst_parent, dst_name):
            dst = f"{dst_parent}/{dst_name}"
            expected_status = self.oracle_fs.move(src, dst)
            if expected_status == "ok":
                await self.workspace.rename(get_path(src), get_path(dst))
            else:
                with pytest.raises(OSError):
                    await self.workspace.rename(get_path(src), get_path(dst))
            return dst

    FSOfflineRestartAndTree.run_as_test()
