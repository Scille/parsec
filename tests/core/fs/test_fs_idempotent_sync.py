# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from string import ascii_lowercase

import pytest
from hypothesis import strategies as st
from hypothesis_trio.stateful import (
    Bundle,
    TrioAsyncioRuleBasedStateMachine,
    initialize,
    invariant,
    multiple,
    rule,
    run_state_machine_as_test,
)

from tests.common import call_with_control

# The point is not to find breaking filenames here, so keep it simple
st_entry_name = st.text(alphabet=ascii_lowercase, min_size=1, max_size=3)
st_entry_type = st.sampled_from(["file", "folder"])


def check_fs_dump(entry):
    assert not entry["need_sync"]
    assert entry["base_version"] in (1, 2)
    if "children" in entry:
        for k, v in entry["children"].items():
            check_fs_dump(v)


@pytest.mark.slow
def test_fs_online_idempotent_sync(
    hypothesis_settings,
    reset_testbed,
    backend_addr,
    backend_factory,
    server_factory,
    user_fs_factory,
    alice,
):
    class FSOnlineIdempotentSync(TrioAsyncioRuleBasedStateMachine):
        BadPath = Bundle("bad_path")
        GoodFilePath = Bundle("good_file_path")
        GoodFolderPath = Bundle("good_folder_path")
        GoodPath = st.one_of(GoodFilePath, GoodFolderPath)

        async def start_user_fs(self, device):
            async def _user_fs_controlled_cb(started_cb):
                async with user_fs_factory(device=device) as user_fs:
                    await started_cb(user_fs=user_fs)

            return await self.get_root_nursery().start(call_with_control, _user_fs_controlled_cb)

        async def start_backend(self):
            async def _backend_controlled_cb(started_cb):
                async with backend_factory() as backend:
                    async with server_factory(backend.handle_client, backend_addr) as server:
                        await started_cb(backend=backend, server=server)

            return await self.get_root_nursery().start(call_with_control, _backend_controlled_cb)

        @property
        def user_fs(self):
            return self.user_fs_controller.user_fs

        @initialize(target=BadPath)
        async def init(self):
            await reset_testbed()
            self.backend_controller = await self.start_backend()
            self.device = alice
            self.user_fs_controller = await self.start_user_fs(alice)

            wid = await self.user_fs.workspace_create("w")
            self.workspace = self.user_fs.get_workspace(wid)
            await self.workspace.touch("/good_file.txt")
            await self.workspace.mkdir("/good_folder")
            await self.workspace.touch("/good_folder/good_sub_file.txt")
            await self.workspace.sync()
            await self.user_fs.sync()

            self.initial_fs_dump = await self.workspace.dump()
            check_fs_dump(self.initial_fs_dump)

            return "/dummy"

        @initialize(target=GoodFolderPath)
        async def init_good_folder_pathes(self):
            return multiple("/", "/good_folder/")

        @initialize(target=GoodFilePath)
        async def init_good_file_pathes(self):
            return multiple("/good_file.txt", "/good_folder/good_sub_file.txt")

        @rule(target=BadPath, type=st_entry_type, bad_parent=BadPath, name=st_entry_name)
        async def try_to_create_bad_path(self, type, bad_parent, name):
            path = f"{bad_parent}/{name}"
            with pytest.raises(FileNotFoundError):
                if type == "file":
                    await self.workspace.touch(path)
                else:
                    await self.workspace.mkdir(path)
            return path

        @rule(type=st_entry_type, path=GoodPath)
        async def try_to_create_already_exists(self, type, path):
            if type == "file":
                if str(path) == "/":
                    with pytest.raises(PermissionError):
                        await self.workspace.mkdir(path)
                else:
                    with pytest.raises(FileExistsError):
                        await self.workspace.mkdir(path)

        @rule(path=BadPath)
        async def try_to_update_file(self, path):
            with pytest.raises(OSError):
                await self.workspace.write_bytes(path, data=b"a", offset=0)

        @rule(path=BadPath)
        async def try_to_delete(self, path):
            with pytest.raises(FileNotFoundError):
                await self.workspace.unlink(path)
            with pytest.raises(FileNotFoundError):
                await self.workspace.rmdir(path)

        @rule(src=BadPath, dst_name=st_entry_name)
        async def try_to_move_bad_src(self, src, dst_name):
            dst = "/%s" % dst_name
            with pytest.raises(OSError):
                await self.workspace.rename(src, dst)

        @rule(src=GoodPath, dst=GoodFolderPath)
        async def try_to_move_bad_dst(self, src, dst):
            # TODO: why so much special cases ?
            if src == dst and src != "/":
                await self.workspace.rename(src, dst)
            else:
                with pytest.raises(OSError):
                    await self.workspace.rename(src, dst)

        @rule(path=GoodPath)
        async def sync(self, path):
            entry_id = await self.workspace.path_id(path)
            await self.workspace.sync_by_id(entry_id)

        @invariant()
        async def check_fs(self):
            try:
                fs_dump = await self.workspace.dump()
            except AttributeError:
                # FS not yet initialized
                pass
            else:
                assert fs_dump == self.initial_fs_dump

    run_state_machine_as_test(FSOnlineIdempotentSync, settings=hypothesis_settings)
