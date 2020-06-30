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

from parsec.core.fs.exceptions import FSWorkspaceNotFoundError
from tests.common import call_with_control, compare_fs_dumps

# The point is not to find breaking filenames here, so keep it simple
st_entry_name = st.text(alphabet=ascii_lowercase, min_size=1, max_size=3)
st_fs = st.sampled_from(["fs_1", "fs_2"])


@pytest.mark.slow
def test_fs_online_concurrent_user(
    hypothesis_settings,
    reset_testbed,
    backend_addr,
    backend_factory,
    server_factory,
    user_fs_factory,
    alice,
    alice2,
):
    class FSOnlineConcurrentUser(TrioAsyncioRuleBasedStateMachine):
        Workspaces = Bundle("workspace")
        FSs = Bundle("fs")

        async def start_user_fs(self, device):
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

        @initialize(target=Workspaces)
        async def init(self):
            await reset_testbed()
            self.device1 = alice
            self.device2 = alice2

            self.backend_controller = await self.start_backend([self.device1, self.device2])
            self.user_fs1_controller = await self.start_user_fs(self.device1)
            self.user_fs2_controller = await self.start_user_fs(self.device2)

            self.wid = await self.user_fs1.workspace_create("w")
            workspace = self.user_fs1.get_workspace(self.wid)
            await workspace.sync()
            await self.user_fs1.sync()
            await self.user_fs2.sync()

            return self.wid, "w"

        @initialize(target=FSs, force_after_init=Workspaces)
        async def register_user_fs1(self, force_after_init):
            return self.user_fs1

        @initialize(target=FSs, force_after_init=Workspaces)
        async def register_user_fs2(self, force_after_init):
            return self.user_fs2

        @rule(target=Workspaces, fs=FSs, name=st_entry_name)
        async def create_workspace(self, fs, name):
            try:
                wid = await fs.workspace_create(name)
                workspace = fs.get_workspace(wid)
                await workspace.sync()
            except AssertionError:
                return "wrong", name
            return wid, name

        @rule(target=Workspaces, fs=FSs, src=Workspaces, dst_name=st_entry_name)
        async def rename_workspace(self, fs, src, dst_name):
            wid, _ = src
            if wid == "wrong":
                return src[0], src[1]
            try:
                await fs.workspace_rename(wid, dst_name)
            except FSWorkspaceNotFoundError:
                pass
            return wid, dst_name

        @rule()
        async def sync(self):
            # Send two syncs in a row given file conflict results are not synced
            # once created

            workspace1 = self.user_fs1.get_workspace(self.wid)
            workspace2 = self.user_fs2.get_workspace(self.wid)

            # Sync 1
            await workspace1.sync()
            await self.user_fs1.sync()
            await self.user_fs1.sync()

            # Sync 2
            await workspace2.sync()
            await self.user_fs2.sync()
            await self.user_fs2.sync()

            # Sync 1
            await workspace1.sync()
            await self.user_fs1.sync()

            fs_dump_1 = await workspace1.dump()
            fs_dump_2 = await workspace2.dump()
            compare_fs_dumps(fs_dump_1, fs_dump_2)

    run_state_machine_as_test(FSOnlineConcurrentUser, settings=hypothesis_settings)
