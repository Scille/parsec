# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from pathlib import Path

import pytest
from hypothesis_trio.stateful import TrioAsyncioRuleBasedStateMachine, run_state_machine_as_test
from pendulum import Pendulum

from parsec.core.fs.remote_loader import RemoteLoader
from parsec.core.fs.storage import WorkspaceStorage
from parsec.core.fs.workspacefs.entry_transactions import EntryTransactions
from parsec.core.fs.workspacefs.file_transactions import FileTransactions
from parsec.core.fs.workspacefs.sync_transactions import SyncTransactions
from parsec.core.types import EntryID, LocalWorkspaceManifest, WorkspaceEntry
from tests.common import call_with_control


@pytest.fixture
def transactions_factory(event_bus, remote_devices_manager_factory):
    async def _transactions_factory(device, backend_cmds, local_storage, cls=SyncTransactions):
        def _get_workspace_entry():
            return workspace_entry

        workspace_entry = WorkspaceEntry.new("test")
        workspace_manifest = LocalWorkspaceManifest.new_placeholder(
            id=workspace_entry.id, now=Pendulum(2000, 1, 1)
        )
        async with local_storage.lock_entry_id(workspace_entry.id):
            await local_storage.set_manifest(workspace_entry.id, workspace_manifest)

        remote_devices_manager = remote_devices_manager_factory(device)
        remote_loader = RemoteLoader(
            device,
            workspace_entry.id,
            _get_workspace_entry,
            backend_cmds,
            remote_devices_manager,
            local_storage,
        )

        return cls(
            workspace_entry.id,
            _get_workspace_entry,
            device,
            local_storage,
            remote_loader,
            event_bus,
        )

    return _transactions_factory


@pytest.fixture
def file_transactions_factory(event_bus, remote_devices_manager_factory, transactions_factory):
    async def _file_transactions_factory(device, backend_cmds, local_storage):
        return await transactions_factory(
            device, backend_cmds=backend_cmds, local_storage=local_storage, cls=FileTransactions
        )

    return _file_transactions_factory


@pytest.fixture
async def alice_transaction_local_storage(alice, persistent_mockup):
    async with WorkspaceStorage.run(alice, Path("/dummy"), EntryID()) as storage:
        yield storage


@pytest.fixture
async def alice_file_transactions(
    file_transactions_factory, alice, alice_backend_cmds, alice_transaction_local_storage
):
    return await file_transactions_factory(
        alice, backend_cmds=alice_backend_cmds, local_storage=alice_transaction_local_storage
    )


@pytest.fixture
def entry_transactions_factory(event_bus, remote_devices_manager_factory, transactions_factory):
    async def _entry_transactions_factory(device, backend_cmds, local_storage):
        return await transactions_factory(
            device, backend_cmds=backend_cmds, local_storage=local_storage, cls=EntryTransactions
        )

    return _entry_transactions_factory


@pytest.fixture
async def alice_entry_transactions(
    entry_transactions_factory, alice, alice_backend_cmds, alice_transaction_local_storage
):
    return await entry_transactions_factory(
        alice, backend_cmds=alice_backend_cmds, local_storage=alice_transaction_local_storage
    )


@pytest.fixture
async def alice_sync_transactions(
    transactions_factory, alice, alice_backend_cmds, alice_transaction_local_storage
):
    return await transactions_factory(
        alice, backend_cmds=alice_backend_cmds, local_storage=alice_transaction_local_storage
    )


@pytest.fixture
def user_fs_offline_state_machine(
    user_fs_factory, persistent_mockup, reset_testbed, hypothesis_settings
):
    class UserFSOfflineStateMachine(TrioAsyncioRuleBasedStateMachine):
        async def start_user_fs(self, device):
            async def _user_fs_controlled_cb(started_cb):
                async with user_fs_factory(device=device) as user_fs:
                    await started_cb(user_fs=user_fs)

            self.user_fs_controller = await self.get_root_nursery().start(
                call_with_control, _user_fs_controlled_cb
            )

        async def stop_user_fs(self):
            try:
                await self.user_fs_controller.stop()
            except AttributeError:
                pass

        async def restart_user_fs(self, device):
            await self.stop_user_fs()
            await self.start_user_fs(device)

        async def reset_user_fs(self, device):
            await self.stop_user_fs()
            persistent_mockup.clear()
            await self.start_user_fs(device)

        async def reset_all(self):
            await self.stop_user_fs()
            await reset_testbed(keep_logs=True)

        @property
        def user_fs(self):
            return self.user_fs_controller.user_fs

        @classmethod
        def run_as_test(cls):
            run_state_machine_as_test(cls, settings=hypothesis_settings)

    return UserFSOfflineStateMachine


@pytest.fixture
def user_fs_online_state_machine(
    user_fs_offline_state_machine, backend_factory, server_factory, backend_addr, reset_testbed
):
    class UserFSOnlineStateMachine(user_fs_offline_state_machine):
        async def start_backend(self):
            async def _backend_controlled_cb(started_cb):
                async with backend_factory() as backend:
                    async with server_factory(backend.handle_client, backend_addr) as server:
                        await started_cb(backend=backend, server=server)

            self.backend_controller = await self.get_root_nursery().start(
                call_with_control, _backend_controlled_cb
            )

        async def stop_backend(self):
            try:
                await self.backend_controller.stop()
            except AttributeError:
                pass

        async def reset_all(self):
            await self.stop_user_fs()
            await self.stop_backend()
            await reset_testbed(keep_logs=True)

        @property
        def backend(self):
            return self.backend_controller.backend

    return UserFSOnlineStateMachine
