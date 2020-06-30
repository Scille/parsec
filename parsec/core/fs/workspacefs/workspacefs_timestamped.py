# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum

from parsec.core.fs.workspacefs.sync_transactions import SyncTransactions
from parsec.core.fs.workspacefs.workspacefs import WorkspaceFS
from parsec.core.types import WorkspaceRole


class WorkspaceFSTimestamped(WorkspaceFS):
    def __init__(self, workspacefs: WorkspaceFS, timestamp: pendulum.Pendulum):
        self.workspace_id = workspacefs.workspace_id
        if isinstance(workspacefs, WorkspaceFSTimestamped):
            self.get_workspace_entry = workspacefs.get_workspace_entry
        else:
            self.get_workspace_entry = self.timestamp_get_entry(workspacefs.get_workspace_entry)
        self.device = workspacefs.device
        self.local_storage = workspacefs.local_storage.to_timestamped(timestamp)
        self.backend_cmds = workspacefs.backend_cmds
        self.event_bus = workspacefs.event_bus
        self.remote_device_manager = workspacefs.remote_device_manager

        self.timestamp = timestamp

        self.remote_loader = workspacefs.remote_loader.to_timestamped(timestamp)
        self.transactions = SyncTransactions(
            self.workspace_id,
            self.get_workspace_entry,
            self.device,
            self.local_storage,
            self.remote_loader,
            self.event_bus,
        )

    def timestamp_get_entry(self, get_original_workspace_entry):
        def get_timestamped_workspace_entry():
            return get_original_workspace_entry().evolve(role=WorkspaceRole.READER)

        return get_timestamped_workspace_entry
