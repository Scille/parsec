# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from hypothesis import strategies as st
from hypothesis_trio.stateful import initialize, rule

from tests.oracles import FileOracle

BLOCK_SIZE = 16
PLAYGROUND_SIZE = BLOCK_SIZE * 10


@pytest.mark.slow
def test_fs_online_rwfile_and_sync(user_fs_online_state_machine, alice):
    class FSOnlineRwFileAndSync(user_fs_online_state_machine):
        @initialize()
        async def init(self):
            await self.reset_all()
            self.device = alice
            await self.start_backend()
            await self.restart_user_fs(self.device)
            self.wid = await self.user_fs.workspace_create("w")
            workspace = self.user_fs.get_workspace(self.wid)
            await workspace.touch("/foo.txt")
            await workspace.sync()
            await self.user_fs.sync()
            self.file_oracle = FileOracle(base_version=1)

        @rule()
        async def restart(self):
            await self.restart_user_fs(self.device)

        @rule()
        async def reset(self):
            await self.reset_user_fs(self.device)
            await self.user_fs.sync()
            self.file_oracle.reset()

        @rule()
        async def sync(self):
            workspace = self.user_fs.get_workspace(self.wid)
            await workspace.sync()
            self.file_oracle.sync()

        @rule(
            size=st.integers(min_value=0, max_value=PLAYGROUND_SIZE),
            offset=st.integers(min_value=0, max_value=PLAYGROUND_SIZE),
        )
        async def atomic_read(self, size, offset):
            workspace = self.user_fs.get_workspace(self.wid)
            content = await workspace.read_bytes("/foo.txt", size=size, offset=offset)
            expected_content = self.file_oracle.read(size, offset)
            assert content == expected_content

        @rule(
            offset=st.integers(min_value=0, max_value=PLAYGROUND_SIZE),
            content=st.binary(max_size=PLAYGROUND_SIZE),
        )
        async def atomic_write(self, offset, content):
            workspace = self.user_fs.get_workspace(self.wid)
            await workspace.write_bytes("/foo.txt", data=content, offset=offset, truncate=False)
            self.file_oracle.write(offset, content)

        @rule(length=st.integers(min_value=0, max_value=PLAYGROUND_SIZE))
        async def atomic_truncate(self, length):
            workspace = self.user_fs.get_workspace(self.wid)
            await workspace.truncate("/foo.txt", length=length)
            self.file_oracle.truncate(length)

        @rule()
        async def stat(self):
            workspace = self.user_fs.get_workspace(self.wid)
            path_info = await workspace.path_info("/foo.txt")
            assert path_info["type"] == "file"
            assert path_info["base_version"] == self.file_oracle.base_version
            assert not path_info["is_placeholder"]
            assert path_info["need_sync"] == self.file_oracle.need_sync
            assert path_info["size"] == self.file_oracle.size

    FSOnlineRwFileAndSync.run_as_test()
