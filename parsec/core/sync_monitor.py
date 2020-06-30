# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import math

import trio
from structlog import get_logger
from trio.hazmat import current_clock

from parsec.core.backend_connection import BackendConnectionError, BackendNotAvailable
from parsec.core.core_events import CoreEvent
from parsec.core.fs import (
    FSBackendOfflineError,
    FSWorkspaceInMaintenance,
    FSWorkspaceNoReadAccess,
    FSWorkspaceNotFoundError,
    FSWorkspaceNoWriteAccess,
)
from parsec.core.types import EntryID, WorkspaceRole

logger = get_logger()

MIN_WAIT = 1
MAX_WAIT = 60
MAINTENANCE_MIN_WAIT = 30
TICK_CRASH_COOLDOWN = 5


async def freeze_sync_monitor_mockpoint():
    """
    Noop function that could be mocked during tests to be able to freeze the
    monitor coroutine running in background
    """
    pass


def timestamp():
    # Use time from trio clock to easily mock it
    return current_clock().current_time()


class LocalChange:
    __slots__ = ("first_changed_on", "last_changed_on", "due_time")

    def __init__(self, now):
        self.first_changed_on = self.last_changed_on = now
        self.due_time = self._compute_due_time()

    def _compute_due_time(self):
        return min(self.last_changed_on + MIN_WAIT, self.first_changed_on + MAX_WAIT)

    def changed(self, changed_on) -> float:
        self.last_changed_on = changed_on
        self.due_time = self._compute_due_time()
        return self.due_time


class SyncContext:
    """
    The SyncContext keeps track of local and remote changes and trigger sync
    accordingly.
    There is two way for it to be informed of remote changes:
    - When online it receive `realm.vlobs_updated` updated events
    - Otherwise (typically when the application starts or when back online after
      an disconnection) it uses the realm's checkpoint stored in the persistent
      storage to get the list of changes (entry id + version) it has missed
    """

    def __init__(self, user_fs, id: EntryID, read_only: bool = False):
        self.user_fs = user_fs
        self.id = id
        self.read_only = read_only
        self.due_time = math.inf
        self._changes_loaded = False
        self._local_changes = {}
        self._remote_changes = set()

    def _sync(self, entry_id: EntryID):
        raise NotImplementedError

    def _get_backend_cmds(self):
        raise NotImplementedError

    def _get_local_storage(self):
        raise NotImplementedError

    def __repr__(self):
        return f"{type(self).__name__}(id={self.id!r})"

    async def _load_changes(self) -> bool:
        if self._changes_loaded:
            return True

        # Initialize due_time so that if we cannot retrieve the changes, we
        # will wait until an external event (most likely a `sharing.updated`)
        # make it worth to retry
        self.due_time = math.inf

        # 1) Fetch new checkpoint and changes
        realm_checkpoint = await self._get_local_storage().get_realm_checkpoint()
        try:
            rep = await self._get_backend_cmds().vlob_poll_changes(self.id, realm_checkpoint)

        except BackendNotAvailable:
            raise

        # Another backend error
        except BackendConnectionError as exc:
            logger.warning("Unexpected backend response during sync bootstrap", exc_info=exc)
            return False

        if rep["status"] == "not_found":
            # Workspace not yet synchronized with backend
            new_checkpoint = 0
            changes = {}
        elif rep["status"] in ("in_maintenance", "not_allowed"):
            return False
        elif rep["status"] != "ok":
            return False
        else:
            new_checkpoint = rep["current_checkpoint"]
            changes = rep["changes"]

        # 2) Store new checkpoint and changes
        await self._get_local_storage().update_realm_checkpoint(new_checkpoint, changes)

        # 3) Compute local and remote changes that need to be synced
        need_sync_local, need_sync_remote = await self._get_local_storage().get_need_sync_entries()
        now = timestamp()
        # Ignore local changes in read only mode
        if not self.read_only:
            self._local_changes = {entry_id: LocalChange(now) for entry_id in need_sync_local}
        self._remote_changes = need_sync_remote

        # 4) Finally refresh due time according to the changes
        self._compute_due_time()

        self._changes_loaded = True
        return True

    def set_local_change(self, entry_id: EntryID) -> bool:
        # Ignore local changes in read only mode
        if self.read_only:
            return

        now = timestamp()
        try:
            new_due_time = self._local_changes[entry_id].changed(now)
        except KeyError:
            local_change = LocalChange(now)
            self._local_changes[entry_id] = local_change
            new_due_time = local_change.due_time

        if new_due_time <= self.due_time:
            self.due_time = new_due_time
            return True

        else:
            return False

    def set_remote_change(self, entry_id: EntryID) -> bool:
        self._remote_changes.add(entry_id)
        self.due_time = timestamp()
        return True

    def _compute_due_time(self, now=None, min_due_time=None):
        if self._remote_changes:
            self.due_time = now or timestamp()
        elif self._local_changes:
            # TODO: index changes by due_time to avoid this O(n) operation
            self.due_time = min(
                change_info.due_time for change_info in self._local_changes.values()
            )
        else:
            self.due_time = math.inf

        if min_due_time:
            self.due_time = max(self.due_time, min_due_time)

        return self.due_time

    async def bootstrap(self) -> float:
        await self._load_changes()
        return self.due_time

    async def tick(self) -> float:
        now = timestamp()
        if self.due_time > now:
            return self.due_time

        # Sync contexts created by `SyncContextStore.get` are bootstrapped
        # lazily on first tick
        if not await self._load_changes():
            return self.due_time

        min_due_time = None

        # Remote changes sync have priority over local changes
        if self._remote_changes:
            entry_id = self._remote_changes.pop()
            try:
                await self._sync(entry_id)
            except FSBackendOfflineError as exc:
                raise BackendNotAvailable from exc
            except FSWorkspaceNoReadAccess:
                # We've just lost the read access to the workspace.
                # This likely means a `sharing.updated` event we soon arrive
                # and destroy this sync context.
                # Until then just pretent nothing happened.
                min_due_time = now + MIN_WAIT
                self._remote_changes.add(entry_id)
            except FSWorkspaceNoWriteAccess:
                # We don't have write access and this entry contains local
                # modifications. Hence we can forget about this change given
                # it's `self._local_changes` role to keep track of local changes.
                pass
            except FSWorkspaceInMaintenance:
                # Not the right time for the sync, retry later
                min_due_time = now + MAINTENANCE_MIN_WAIT
                self._remote_changes.add(entry_id)

        elif self._local_changes:
            entry_id = next(
                (
                    entry_id
                    for entry_id, change_info in self._local_changes.items()
                    if change_info.due_time <= now
                ),
                None,
            )
            if entry_id:
                del self._local_changes[entry_id]
                try:
                    await self._sync(entry_id)
                except FSBackendOfflineError as exc:
                    raise BackendNotAvailable from exc
                except (FSWorkspaceNoReadAccess, FSWorkspaceNoWriteAccess):
                    # We've just lost the write access to the workspace, and
                    # the corresponding `sharing.updated` event hasn't updated
                    # the `read_only` flag yet.
                    # We keep track of the change (given we may be given back
                    # the write access in the future) but pretent it just accured
                    # to avoid a busy sync loop until `read_only` flag is updated.
                    self._local_changes[entry_id] = LocalChange(now)
                except FSWorkspaceInMaintenance:
                    # Not the right time for the sync, retry later
                    min_due_time = now + MAINTENANCE_MIN_WAIT
                    self._local_changes[entry_id] = LocalChange(now)

                # This is where we plug our vacuuming routine
                # as it corresponds to a fresh synchronized state
                if not self._local_changes:
                    await self._get_local_storage().run_vacuum()

        self._compute_due_time(now=now, min_due_time=min_due_time)
        return self.due_time


class WorkspaceSyncContext(SyncContext):
    def __init__(self, user_fs, id: EntryID):
        self.workspace = user_fs.get_workspace(id)
        read_only = self.workspace.get_workspace_entry().role == WorkspaceRole.READER
        super().__init__(user_fs, id, read_only=read_only)

    async def _sync(self, entry_id: EntryID):
        # No recursion here: only the manifest that has changed
        # (remotely or locally) should get synchronized
        await self.workspace.sync_by_id(entry_id, recursive=False)

    def _get_backend_cmds(self):
        return self.workspace.backend_cmds

    def _get_local_storage(self):
        return self.workspace.local_storage


class UserManifestSyncContext(SyncContext):
    async def _sync(self, entry_id: EntryID):
        assert entry_id == self.id
        await self.user_fs.sync()

    def _get_backend_cmds(self):
        return self.user_fs.backend_cmds

    def _get_local_storage(self):
        return self.user_fs.storage


class SyncContextStore:
    """
    Hold the `SyncContext` and lazily create them on need (typically
    when a newly created workspace is modified for the first time)
    """

    def __init__(self, user_fs):
        self.user_fs = user_fs
        self._ctxs = {}

    def iter(self):
        return self._ctxs.copy().values()

    def get(self, entry_id):
        try:
            return self._ctxs[entry_id]
        except KeyError:
            if entry_id == self.user_fs.user_manifest_id:
                ctx = UserManifestSyncContext(self.user_fs, entry_id)
            else:
                try:
                    ctx = WorkspaceSyncContext(self.user_fs, entry_id)
                except FSWorkspaceNotFoundError:
                    # It's possible the workspace is not yet available
                    # (this can happen when a workspace is just shared with
                    # us, hence we receive vlob updated events before having
                    # added the workspace entry to our user manifest)
                    return None
            self._ctxs[entry_id] = ctx
            return ctx

    def discard(self, entry_id):
        self._ctxs.pop(entry_id, None)


async def monitor_sync(user_fs, event_bus, task_status):
    ctxs = SyncContextStore(user_fs)
    early_wakeup = trio.Event()

    def _trigger_early_wakeup():
        early_wakeup.set()
        # Don't wait for the *actual* awakening to change the status to
        # avoid having a period of time when the awakening is scheduled but
        # not yet notified to task_status
        task_status.awake()

    def _on_entry_updated(event, id, workspace_id=None):
        if workspace_id is None:
            # User manifest
            assert id == user_fs.user_manifest_id
            ctx = ctxs.get(id)
        else:
            ctx = ctxs.get(workspace_id)
        if ctx and ctx.set_local_change(id):
            _trigger_early_wakeup()

    def _on_realm_vlobs_updated(sender, realm_id, checkpoint, src_id, src_version):
        ctx = ctxs.get(realm_id)
        if ctx and ctx.set_remote_change(src_id):
            _trigger_early_wakeup()

    def _on_sharing_updated(sender, new_entry, previous_entry):
        # If role have changed we have to reset the sync context given
        # behavior could have changed a lot (e.g. switching to/from read-only)
        ctxs.discard(new_entry.id)
        if new_entry.role is not None:
            ctx = ctxs.get(new_entry.id)
            if ctx:
                # Change the due_time so the context understants the early
                # wakeup is for him
                ctx.due_time = timestamp()
                _trigger_early_wakeup()

    async def _ctx_action(ctx, meth):
        try:
            return await getattr(ctx, meth)()
        except BackendNotAvailable:
            raise
        except Exception:
            logger.exception("Sync monitor has crashed", workspace_id=ctx.id)
            # Reset sync context which is now in an undefined state
            ctxs.discard(ctx.id)
            ctx = ctxs.get(ctx.id)
            if ctx:
                # Add small cooldown just to be sure not end up in a crazy busy error loop
                ctx.due_time = timestamp() + TICK_CRASH_COOLDOWN
                return ctx.due_time
            else:
                return math.inf

    with event_bus.connect_in_context(
        (CoreEvent.FS_ENTRY_UPDATED, _on_entry_updated),
        (CoreEvent.BACKEND_REALM_VLOBS_UPDATED, _on_realm_vlobs_updated),
        (CoreEvent.SHARING_UPDATED, _on_sharing_updated),
    ):
        due_times = []
        # Init userfs sync context
        ctx = ctxs.get(user_fs.user_manifest_id)
        due_times.append(await _ctx_action(ctx, "bootstrap"))
        # Init workspaces sync context
        user_manifest = user_fs.get_user_manifest()
        for entry in user_manifest.workspaces:
            if entry.role is not None:
                ctx = ctxs.get(entry.id)
                if ctx:
                    due_times.append(await _ctx_action(ctx, "bootstrap"))

        task_status.started()
        while True:
            next_due_time = min(due_times)
            if next_due_time == math.inf:
                task_status.idle()
            with trio.move_on_at(next_due_time) as cancel_scope:
                await early_wakeup.wait()
                early_wakeup = trio.Event()
            # In case of early wakeup, `_trigger_early_wakeup` is responsible
            # for calling `task_status.awake()`
            if cancel_scope.cancelled_caught:
                task_status.awake()
            due_times.clear()
            await freeze_sync_monitor_mockpoint()
            for ctx in ctxs.iter():
                due_times.append(await _ctx_action(ctx, "tick"))
