# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Tuple

from async_generator import asynccontextmanager

from parsec.core.core_events import CoreEvent
from parsec.core.fs.exceptions import (
    FSCrossDeviceError,
    FSDirectoryNotEmptyError,
    FSFileExistsError,
    FSFileNotFoundError,
    FSIsADirectoryError,
    FSLocalMissError,
    FSNoAccessError,
    FSNotADirectoryError,
    FSPermissionError,
    FSReadOnlyError,
)
from parsec.core.fs.utils import is_file_manifest, is_folder_manifest, is_folderish_manifest
from parsec.core.fs.workspacefs.file_transactions import FileTransactions
from parsec.core.types import (
    EntryID,
    FileDescriptor,
    FsPath,
    LocalFileManifest,
    LocalFolderManifest,
    LocalManifest,
    WorkspaceRole,
)

WRITE_RIGHT_ROLES = (WorkspaceRole.OWNER, WorkspaceRole.MANAGER, WorkspaceRole.CONTRIBUTOR)


class EntryTransactions(FileTransactions):

    # Right management helper

    def check_read_rights(self, path: FsPath):
        if self.get_workspace_entry().role is None:
            raise FSNoAccessError(filename=path)

    def check_write_rights(self, path: FsPath):
        self.check_read_rights(path)
        if self.get_workspace_entry().role not in WRITE_RIGHT_ROLES:
            raise FSReadOnlyError(filename=path)

    # Look-up helpers

    async def _get_manifest(self, entry_id: EntryID) -> LocalManifest:
        try:
            return await self.local_storage.get_manifest(entry_id)
        except FSLocalMissError as exc:
            remote_manifest = await self.remote_loader.load_manifest(exc.id)
            return LocalManifest.from_remote(remote_manifest)

    @asynccontextmanager
    async def _load_and_lock_manifest(self, entry_id: EntryID):
        async with self.local_storage.lock_entry_id(entry_id):
            try:
                local_manifest = await self.local_storage.get_manifest(entry_id)
            except FSLocalMissError as exc:
                remote_manifest = await self.remote_loader.load_manifest(exc.id)
                local_manifest = LocalManifest.from_remote(remote_manifest)
                await self.local_storage.set_manifest(entry_id, local_manifest)
            yield local_manifest

    async def _load_manifest(self, entry_id: EntryID) -> LocalManifest:
        async with self._load_and_lock_manifest(entry_id) as manifest:
            return manifest

    @asynccontextmanager
    async def _lock_manifest_from_path(self, path: FsPath) -> LocalManifest:
        # Root entry_id and manifest
        entry_id = self.workspace_id

        # Follow the path
        for name in path.parts:
            manifest = await self._load_manifest(entry_id)
            if is_file_manifest(manifest):
                raise FSNotADirectoryError(filename=path)
            try:
                entry_id = manifest.children[name]
            except (AttributeError, KeyError):
                raise FSFileNotFoundError(filename=path)

        # Lock entry
        async with self._load_and_lock_manifest(entry_id) as manifest:
            yield manifest

    async def _get_manifest_from_path(self, path: FsPath) -> LocalManifest:
        async with self._lock_manifest_from_path(path) as manifest:
            return manifest

    @asynccontextmanager
    async def _lock_parent_manifest_from_path(
        self, path: FsPath
    ) -> Tuple[LocalManifest, LocalManifest]:
        # This is the most complicated locking scenario.
        # It requires locking the parent of the given entry and the entry itself
        # if it exists.

        # This is done in a two step process:
        # - 1. Lock the parent (it must exist). While the parent is locked, no
        #   children can be added, renamed or removed.
        # - 2. Lock the children if exists. It it doesn't, there is nothing to lock
        #   since the parent lock guarentees that it is not going to be added while
        #   using the context.

        # This double locking is only required for a single use case: the overwriting
        # of empty directory during a move. We have to make sure that no one adds
        # something to the directory while it is being overwritten.
        # If read/write locks were to be implemented, the parent would be write locked
        # and the child read locked. This means that despite locking two entries, only
        # a single entry is modified at a time.

        # Source is root
        if path.is_root():
            raise FSPermissionError(filename=str(path))

        # Loop over attempts
        while True:

            # Lock parent first
            async with self._lock_manifest_from_path(path.parent) as parent:

                # Parent is not a directory
                if not is_folderish_manifest(parent):
                    raise FSNotADirectoryError(filename=path.parent)

                # Child doesn't exist
                if path.name not in parent.children:
                    yield parent, None
                    return

                # Child exists
                entry_id = parent.children[path.name]
                try:
                    async with self.local_storage.lock_manifest(entry_id) as manifest:
                        yield parent, manifest
                        return

                # Child is not available
                except FSLocalMissError as exc:
                    assert exc.id == entry_id

            # Release the lock and download the child manifest
            await self._load_manifest(entry_id)

    # Transactions

    async def entry_info(self, path: FsPath) -> dict:
        # Check read rights
        self.check_read_rights(path)

        # Fetch data
        manifest = await self._get_manifest_from_path(path)

        # General stats
        stats = {
            "id": manifest.id,
            "created": manifest.created,
            "updated": manifest.updated,
            "base_version": manifest.base_version,
            "is_placeholder": manifest.is_placeholder,
            "need_sync": manifest.need_sync,
        }

        # File/folder specific stats
        if is_file_manifest(manifest):
            stats["type"] = "file"
            stats["size"] = manifest.size
        else:
            stats["type"] = "folder"
            stats["children"] = sorted(manifest.children.keys())

        return stats

    async def entry_rename(
        self, source: FsPath, destination: FsPath, overwrite: bool = True
    ) -> EntryID:
        # Check write rights
        self.check_write_rights(source)

        # Source is root
        if source.is_root():
            raise FSPermissionError(filename=source)

        # Destination is root
        if destination.is_root():
            raise FSPermissionError(filename=destination)

        # Cross-directory renaming is not supported
        if source.parent != destination.parent:
            raise FSCrossDeviceError(filename=source, filename2=destination)

        # Pre-fetch the source if necessary
        if overwrite:
            await self._get_manifest_from_path(source)

        # Fetch and lock
        async with self._lock_parent_manifest_from_path(destination) as (parent, child):

            # Source does not exist
            if source.name not in parent.children:
                raise FSFileNotFoundError(filename=source)
            source_entry_id = parent.children[source.name]

            # Source and destination are the same
            if source.name == destination.name:
                return

            # Destination already exists
            if not overwrite and child is not None:
                raise FSFileExistsError(filename=destination)

            # Overwrite logic
            if overwrite and child is not None:
                source_manifest = await self._get_manifest(source_entry_id)

                # Overwrite a file
                if is_file_manifest(source_manifest):

                    # Destination is a folder
                    if is_folder_manifest(child):
                        raise FSIsADirectoryError(filename=destination)

                # Overwrite a folder
                if is_folder_manifest(source_manifest):

                    # Destination is not a folder
                    if is_file_manifest(child):
                        raise FSNotADirectoryError(filename=destination)

                    # Destination is not empty
                    if child.children:
                        raise FSDirectoryNotEmptyError(filename=destination)

            # Create new manifest
            new_parent = parent.evolve_children_and_mark_updated(
                {destination.name: source_entry_id, source.name: None}
            )

            # Atomic change
            await self.local_storage.set_manifest(parent.id, new_parent)

        # Send event
        self._send_event(CoreEvent.FS_ENTRY_UPDATED, id=parent.id)

        # Return the entry id of the renamed entry
        return parent.children[source.name]

    async def folder_delete(self, path: FsPath) -> EntryID:
        # Check write rights
        self.check_write_rights(path)

        # Fetch and lock
        async with self._lock_parent_manifest_from_path(path) as (parent, child):

            # Entry doesn't exist
            if child is None:
                raise FSFileNotFoundError(filename=path)

            # Not a directory
            if not is_folderish_manifest(child):
                raise FSNotADirectoryError(filename=path)

            # Directory not empty
            if child.children:
                raise FSDirectoryNotEmptyError(filename=path)

            # Create new manifest
            new_parent = parent.evolve_children_and_mark_updated({path.name: None})

            # Atomic change
            await self.local_storage.set_manifest(parent.id, new_parent)

        # Send event
        self._send_event(CoreEvent.FS_ENTRY_UPDATED, id=parent.id)

        # Return the entry id of the removed folder
        return child.id

    async def file_delete(self, path: FsPath) -> EntryID:
        # Check write rights
        self.check_write_rights(path)

        # Fetch and lock
        async with self._lock_parent_manifest_from_path(path) as (parent, child):

            # Entry doesn't exist
            if child is None:
                raise FSFileNotFoundError(filename=path)

            # Not a file
            if not is_file_manifest(child):
                raise FSIsADirectoryError(filename=path)

            # Create new manifest
            new_parent = parent.evolve_children_and_mark_updated({path.name: None})

            # Atomic change
            await self.local_storage.set_manifest(parent.id, new_parent)

        # Send event
        self._send_event(CoreEvent.FS_ENTRY_UPDATED, id=parent.id)

        # Return the entry id of the deleted file
        return child.id

    async def folder_create(self, path: FsPath) -> EntryID:
        # Check write rights
        self.check_write_rights(path)

        # Lock parent and child
        async with self._lock_parent_manifest_from_path(path) as (parent, child):

            # Destination already exists
            if child is not None:
                raise FSFileExistsError(filename=path)

            # Create folder
            child = LocalFolderManifest.new_placeholder(parent=parent.id)

            # New parent manifest
            new_parent = parent.evolve_children_and_mark_updated({path.name: child.id})

            # ~ Atomic change
            await self.local_storage.set_manifest(child.id, child, check_lock_status=False)
            await self.local_storage.set_manifest(parent.id, new_parent)

        # Send events
        self._send_event(CoreEvent.FS_ENTRY_UPDATED, id=parent.id)
        self._send_event(CoreEvent.FS_ENTRY_UPDATED, id=child.id)

        # Return the entry id of the created folder
        return child.id

    async def file_create(self, path: FsPath, open=True) -> Tuple[EntryID, FileDescriptor]:
        # Check write rights
        self.check_write_rights(path)

        # Lock parent in write mode
        async with self._lock_parent_manifest_from_path(path) as (parent, child):

            # Destination already exists
            if child is not None:
                raise FSFileExistsError(filename=path)

            # Create file
            child = LocalFileManifest.new_placeholder(parent=parent.id)

            # New parent manifest
            new_parent = parent.evolve_children_and_mark_updated({path.name: child.id})

            # ~ Atomic change
            await self.local_storage.set_manifest(child.id, child, check_lock_status=False)
            await self.local_storage.set_manifest(parent.id, new_parent)
            fd = self.local_storage.create_file_descriptor(child) if open else None

        # Send events
        self._send_event(CoreEvent.FS_ENTRY_UPDATED, id=parent.id)
        self._send_event(CoreEvent.FS_ENTRY_UPDATED, id=child.id)

        # Return the entry id of the created file and the file descriptor
        return child.id, fd

    async def file_open(self, path: FsPath, mode="rw") -> Tuple[EntryID, FileDescriptor]:
        # Check read and write rights
        if "w" in mode:
            self.check_write_rights(path)
        else:
            self.check_read_rights(path)

        # Lock path in read mode
        async with self._lock_manifest_from_path(path) as manifest:

            # Not a file
            if not is_file_manifest(manifest):
                raise FSIsADirectoryError(filename=path)

            # Return the entry id of the open file and the file descriptor
            return manifest.id, self.local_storage.create_file_descriptor(manifest)

    async def file_resize(self, path: FsPath, length: int) -> EntryID:
        # Check write rights
        self.check_write_rights(path)

        # Lock manifest
        async with self._lock_manifest_from_path(path) as manifest:

            # Not a file
            if not is_file_manifest(manifest):
                raise FSIsADirectoryError(filename=path)

            # Perform resize
            await self._manifest_resize(manifest, length)

            # Return entry id
            return manifest.id
