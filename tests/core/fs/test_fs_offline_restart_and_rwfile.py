# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from hypothesis import strategies as st
from hypothesis_trio.stateful import initialize, rule

from tests.oracles import FileOracle

BLOCK_SIZE = 16
PLAYGROUND_SIZE = BLOCK_SIZE * 10


@pytest.mark.slow
def test_fs_offline_restart_and_rwfile(user_fs_offline_state_machine, alice):
    class FSOfflineRestartAndRWFile(user_fs_offline_state_machine):
        @initialize()
        async def init(self):
            await self.reset_all()
            self.device = alice
            await self.restart_user_fs(self.device)
            self.wid = await self.user_fs.workspace_create("w")
            self.workspace = self.user_fs.get_workspace(self.wid)
            await self.workspace.touch("/foo.txt")
            self.file_oracle = FileOracle()

        @rule()
        async def restart(self):
            await self.restart_user_fs(self.device)
            self.workspace = self.user_fs.get_workspace(self.wid)

        @rule(
            size=st.integers(min_value=0, max_value=PLAYGROUND_SIZE),
            offset=st.integers(min_value=0, max_value=PLAYGROUND_SIZE),
        )
        async def atomic_read(self, size, offset):
            content = await self.workspace.read_bytes("/foo.txt", size=size, offset=offset)
            expected_content = self.file_oracle.read(size, offset)
            assert content == expected_content

        @rule(
            offset=st.integers(min_value=0, max_value=PLAYGROUND_SIZE),
            content=st.binary(max_size=PLAYGROUND_SIZE),
        )
        async def atomic_write(self, offset, content):
            await self.workspace.write_bytes(
                "/foo.txt", data=content, offset=offset, truncate=False
            )
            self.file_oracle.write(offset, content)

        @rule(length=st.integers(min_value=0, max_value=PLAYGROUND_SIZE))
        async def atomic_truncate(self, length):
            await self.workspace.truncate("/foo.txt", length=length)
            self.file_oracle.truncate(length)

    FSOfflineRestartAndRWFile.run_as_test()
