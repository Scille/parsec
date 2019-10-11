# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio
from typing import Dict, Tuple
from pendulum import Pendulum

from parsec.api.protocol import DeviceID
from parsec.core.types import FsPath, EntryID
from parsec.core.fs.utils import is_file_manifest, is_folder_manifest, is_workspace_manifest
from parsec.core.fs.exceptions import (
    FSRemoteManifestNotFoundBadVersion,
    FSLocalMissError,
    FSEntryNotFound,
)


async def list_versions(
    workspacefs, path: FsPath
) -> Dict[
    Tuple[EntryID, int, Pendulum, Pendulum],
    Tuple[Tuple[DeviceID, Pendulum, bool, int], FsPath, FsPath],
]:
    """
    Raises:
        FSError
        FSBackendOfflineError
        FSWorkspaceInMaintenance
        FSRemoteManifestNotFound
    """
    # Each key is an entry_id, each value is a new dict with version as key and value as tuple(
    #     updated_timestamp, last_known_timestamp, manifest
    # )
    manifest_cache = {}

    async def _load_manifest_or_cached(entry_id: EntryID, version=None, timestamp=None):
        try:
            if version:
                return manifest_cache[entry_id][version][2]
            if timestamp:
                return max(
                    (
                        t
                        for t in manifest_cache[entry_id].values()
                        if t[1] and t[0] < timestamp <= t[1]
                    ),
                    key=lambda t: t[0],
                )[2]
        except (ValueError, KeyError):
            pass
        manifest = await workspacefs.remote_loader.load_manifest(
            entry_id, version=version, timestamp=timestamp
        )
        if manifest.id not in manifest_cache:
            manifest_cache[manifest.id] = {}
        if manifest.version not in manifest_cache[manifest.id]:
            manifest_cache[manifest.id][manifest.version] = (
                manifest.updated,
                timestamp if timestamp else None,
                manifest,
            )
        elif timestamp:
            if (
                manifest_cache[manifest.id][manifest.version][1] is None
                or timestamp > manifest_cache[manifest.id][manifest.version][1]
            ):
                manifest_cache[manifest.id][manifest.version] = (
                    manifest.updated,
                    timestamp,
                    manifest,
                )
        return manifest

    async def _get_past_path(entry_id: EntryID, version=None, timestamp=None) -> FsPath:

        # Get first manifest
        try:
            current_id = entry_id
            current_manifest = await _load_manifest_or_cached(
                current_id, version=version, timestamp=timestamp
            )
        except FSLocalMissError:
            raise FSEntryNotFound(entry_id)

        # Loop over parts
        parts = []
        while not is_workspace_manifest(current_manifest):

            # Get the manifest
            try:
                parent_manifest = await _load_manifest_or_cached(
                    current_manifest.parent, version=version, timestamp=timestamp
                )
            except FSLocalMissError:
                raise FSEntryNotFound(entry_id)

            # Find the child name
            try:
                name = next(
                    name
                    for name, child_id in parent_manifest.children.items()
                    if child_id == current_id
                )
            except StopIteration:
                raise FSEntryNotFound(entry_id)
            else:
                parts.append(name)

            # Continue until root is found
            current_id = current_manifest.parent
            current_manifest = parent_manifest

        # Return the path
        return FsPath("/" + "/".join(reversed(parts)))

    async def _populate_tree_load(
        nursery,
        target: FsPath,
        path_level: int,
        tree: dict,
        entry_id: EntryID,
        early: Pendulum,
        late: Pendulum,
        version_number: int,
        next_version_number: int,
    ):
        if early > late:
            return
        manifest = await _load_manifest_or_cached(entry_id, version=version_number)
        data = [
            (
                manifest.author,
                manifest.updated,
                is_folder_manifest(manifest),
                None if not is_file_manifest(manifest) else manifest.size,
            ),
            None,
            None,
            None,
        ]

        if len(target.parts) == path_level + 1:

            async def _populate_path_w_index(data, index, entry_id, timestamp):
                try:
                    data[index] = await _get_past_path(entry_id, timestamp=timestamp)
                except (FSRemoteManifestNotFoundBadVersion, FSEntryNotFound):
                    pass

            # TODO : Use future manifest source field to follow files and directories
            async with trio.open_nursery() as child_nursery:
                child_nursery.start_soon(
                    _populate_path_w_index, data, 1, entry_id, early.add(microseconds=-1)
                )
                child_nursery.start_soon(_populate_path_w_index, data, 2, entry_id, late)
                child_nursery.start_soon(_populate_path_w_index, data, 3, entry_id, early)
            tree[(manifest.id, manifest.version, early, late)] = (
                data[0],
                data[1] if data[1] != data[3] else None,
                data[2] if data[2] != data[3] else None,
            )
        else:
            if not is_file_manifest(manifest):
                for child_name, child_id in manifest.children.items():
                    if child_name == target.parts[path_level + 1]:
                        return await _populate_tree_list_versions(
                            nursery,
                            target,
                            path_level + 1,
                            tree,
                            child_id,
                            early if early > manifest.updated else manifest.updated,
                            late,
                        )
            else:
                pass  # TODO : Broken path. What to do?

    async def _populate_tree_list_versions(
        nursery,
        target: FsPath,
        path_level: int,
        tree: dict,
        entry_id: EntryID,
        early: Pendulum,
        late: Pendulum,
    ):
        # TODO : Check if directory, melt the same entries through different parent
        versions = await workspacefs.remote_loader.list_versions(entry_id)
        for version, (timestamp, creator) in versions.items():
            next_version = min((v for v in versions if v > version), default=None)
            nursery.start_soon(
                _populate_tree_load,
                nursery,
                target,
                path_level,
                tree,
                entry_id,
                max(early, timestamp),
                late if next_version not in versions else min(late, versions[next_version][0]),
                version,
                next_version,
            )

    return_tree = {}
    root_manifest = await workspacefs.transactions._get_manifest(workspacefs.workspace_id)
    async with trio.open_nursery() as nursery:
        nursery.start_soon(
            _populate_tree_list_versions,
            nursery,
            path,
            0,
            return_tree,
            root_manifest.id,
            root_manifest.created,
            Pendulum.now(),
        )
    return {
        item[0]: item[1]
        for item in sorted(
            list(return_tree.items()), key=lambda item: (item[0][3], item[0][0], item[0][1])
        )
    }
