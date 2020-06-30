# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from string import ascii_lowercase

import pytest
from hypothesis import strategies as st
from hypothesis_trio.stateful import (
    Bundle,
    TrioAsyncioRuleBasedStateMachine,
    initialize,
    rule,
    run_state_machine_as_test,
)

from tests.common import call_with_control, compare_fs_dumps

# The point is not to find breaking filenames here, so keep it simple
st_entry_name = st.text(alphabet=ascii_lowercase, min_size=1, max_size=3)
st_fs = st.sampled_from(["fs_1", "fs_2"])


@pytest.mark.slow
def test_fs_online_concurrent_tree_and_sync(
    hypothesis_settings,
    reset_testbed,
    backend_addr,
    backend_factory,
    server_factory,
    user_fs_factory,
    alice,
    alice2,
):
    class FSOnlineConcurrentTreeAndSync(TrioAsyncioRuleBasedStateMachine):
        Files = Bundle("file")
        Folders = Bundle("folder")
        FSs = Bundle("fs")

        async def start_fs(self, device):
            async def _user_fs_controlled_cb(started_cb):
                async with user_fs_factory(device=device) as fs:
                    await started_cb(fs=fs)

            return await self.get_root_nursery().start(call_with_control, _user_fs_controlled_cb)

        async def start_backend(self, devices):
            async def _backend_controlled_cb(started_cb):
                async with backend_factory() as backend:
                    async with server_factory(backend.handle_client, backend_addr) as server:
                        await started_cb(backend=backend, server=server)

            return await self.get_root_nursery().start(call_with_control, _backend_controlled_cb)

        @property
        def user_fs1(self):
            return self.user_fs1_controller.fs

        @property
        def user_fs2(self):
            return self.user_fs2_controller.fs

        @initialize(target=Folders)
        async def init(self):
            await reset_testbed()
            self.device1 = alice
            self.device2 = alice2

            self.backend_controller = await self.start_backend([self.device1, self.device2])
            self.user_fs1_controller = await self.start_fs(self.device1)
            self.user_fs2_controller = await self.start_fs(self.device2)

            self.wid = await self.user_fs1.workspace_create("w")
            workspace = self.user_fs1.get_workspace(self.wid)
            await workspace.sync()
            await self.user_fs1.sync()
            await self.user_fs2.sync()

            return "/"

        @initialize(target=FSs, force_after_init=Folders)
        async def register_user_fs1(self, force_after_init):
            return self.user_fs1

        @initialize(target=FSs, force_after_init=Folders)
        async def register_user_fs2(self, force_after_init):
            return self.user_fs2

        @rule(target=Files, fs=FSs, parent=Folders, name=st_entry_name)
        async def create_file(self, fs, parent, name):
            path = f"{parent}/{name}"
            workspace = fs.get_workspace(self.wid)
            try:
                await workspace.touch(path=path)
            except OSError:
                pass
            return path

        @rule(target=Folders, fs=FSs, parent=Folders, name=st_entry_name)
        async def create_folder(self, fs, parent, name):
            path = f"{parent}/{name}"
            workspace = fs.get_workspace(self.wid)
            try:
                await workspace.mkdir(path=path)
            except OSError:
                pass
            return path

        @rule(fs=FSs, path=Files)
        async def update_file(self, fs, path):
            workspace = fs.get_workspace(self.wid)
            try:
                await workspace.write_bytes(path, offset=0, data=b"a")
            except OSError:
                pass

        @rule(fs=FSs, path=Files)
        async def delete_file(self, fs, path):
            workspace = fs.get_workspace(self.wid)
            try:
                await workspace.unlink(path=path)
            except OSError:
                pass
            return path

        @rule(fs=FSs, path=Folders)
        async def delete_folder(self, fs, path):
            workspace = fs.get_workspace(self.wid)
            try:
                await workspace.rmdir(path=path)
            except OSError:
                pass
            return path

        @rule(target=Files, fs=FSs, src=Files, dst_parent=Folders, dst_name=st_entry_name)
        async def move_file(self, fs, src, dst_parent, dst_name):
            dst = f"{dst_parent}/{dst_name}"
            workspace = fs.get_workspace(self.wid)
            try:
                await workspace.move(src, dst)
            except OSError:
                pass
            return dst

        @rule(target=Folders, fs=FSs, src=Folders, dst_parent=Folders, dst_name=st_entry_name)
        async def move_folder(self, fs, src, dst_parent, dst_name):
            dst = f"{dst_parent}/{dst_name}"
            workspace = fs.get_workspace(self.wid)
            try:
                await workspace.move(src, dst)
            except OSError:
                pass
            return dst

        @rule()
        async def sync_all_the_files(self):
            # Less than 4 retries causes the following test case to fail:
            # ```python
            # state = FSOnlineConcurrentTreeAndSync()
            # async def steps():
            #     v1 = await state.init()
            #     v2 = await state.register_user_fs1(force_after_init=v1)
            #     v3 = await state.register_user_fs2(force_after_init=v1)
            #     v4 = await state.create_file(fs=v3, name='b', parent=v1)
            #     await state.sync_all_the_files()
            #     await state.update_file(fs=v3, path=v4)
            #     await state.update_file(fs=v2, path=v4)
            #     v5 = await state.create_file(fs=v3, name='a', parent=v1)
            #     v6 = await state.create_file(fs=v3, name='a', parent=v1)
            #     v7 = await state.move_file(dst_name='a', dst_parent=v1, fs=v2, src=v4)
            #     await state.sync_all_the_files()
            #     await state.teardown()
            # state.trio_run(steps)
            # ```
            # for fs in [self.user_fs1, self.user_fs2]:
            #     workspace = fs.get_workspace(self.wid)
            retries = 4
            workspace1 = self.user_fs1.get_workspace(self.wid)
            workspace2 = self.user_fs2.get_workspace(self.wid)
            for _ in range(retries):
                await workspace1.sync()
                await workspace2.sync()
                await self.user_fs1.sync()
                await self.user_fs2.sync()

            user_fs_dump_1 = await workspace1.dump()
            user_fs_dump_2 = await workspace2.dump()
            compare_fs_dumps(user_fs_dump_1, user_fs_dump_2)

    run_state_machine_as_test(FSOnlineConcurrentTreeAndSync, settings=hypothesis_settings)
