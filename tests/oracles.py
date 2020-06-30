# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def oracle_fs_factory(tmpdir):
    class OracleFS:
        def __init__(self, base_path):
            self.base = Path(base_path)
            self.base.mkdir(parents=True)
            self.root = self.base / "root"
            self.root.mkdir()
            # Root oracle can no longer be removed this way
            self.base.chmod(0o500)
            if os.name == "nt":
                self.root.chmod(0o500)
            self.entries_stats = {}
            self._register_stat(self.root, "root")

        def _is_workspace(self, path):
            return len(path.relative_to(self.root).parts) == 1

        def _cook_path(self, path):
            assert path[0] == "/"
            return self.root / path[1:]

        def _register_stat(self, path, type):
            self.entries_stats[path] = {
                "type": type,
                "base_version": 0,
                "is_placeholder": True,
                "need_sync": True,
            }

        def dump(self):
            content = []

            def _recursive_dump(path):
                for child in path.iterdir():
                    entry_name = self._relative_path(child)
                    if child.is_dir():
                        _recursive_dump(child)
                        entry_name += "/"
                    stat = self.entries_stats[child]
                    content.append((entry_name, stat["base_version"], stat["need_sync"]))

            _recursive_dump(self.root)
            return sorted(content)

        def copy(self):
            new_oracle = _oracle_fs_factory()
            for path, stat in self.entries_stats.items():
                new_path = new_oracle.root / path.relative_to(self.root)
                new_oracle.entries_stats[new_path] = stat.copy()
            # copytree requires the target folder doesn't exist
            new_oracle.base.chmod(0o700)
            new_oracle.root.rmdir()
            shutil.copytree(self.root, new_oracle.root)
            new_oracle.base.chmod(0o500)
            return new_oracle

        def create_file(self, path):
            path = self._cook_path(path)
            if self._is_workspace(path):
                return "invalid_path"
            try:
                path.touch(exist_ok=False)
            except OSError:
                return "invalid_path"
            self._register_stat(path, "file")
            self.entries_stats[path.parent]["need_sync"] = True
            return "ok"

        def create_folder(self, path):
            return self._create_folder(path)

        def _create_folder(self, path, workspace=False):
            path = self._cook_path(path)

            if workspace:
                if not self._is_workspace(path):
                    return "invalid_path"
            else:
                if self._is_workspace(path):
                    return "invalid_path"

            try:
                path.mkdir(exist_ok=False)
            except OSError:
                return "invalid_path"
            self._register_stat(path, "folder")
            self.entries_stats[path.parent]["need_sync"] = True
            return "ok"

        def create_workspace(self, path):
            return self._create_folder(path, workspace=True)

        def unlink(self, path):
            path = self._cook_path(path)
            if self._is_workspace(path):
                return "invalid_path"
            try:
                path.unlink()
            except OSError:
                return "invalid_path"
            del self.entries_stats[path]
            self.entries_stats[path.parent]["need_sync"] = True
            return "ok"

        def rmdir(self, path):
            path = self._cook_path(path)
            if self._is_workspace(path):
                return "invalid_path"
            try:
                path.rmdir()
            except OSError:
                return "invalid_path"
            self._delete_stats(path)
            self.entries_stats[path.parent]["need_sync"] = True
            return "ok"

        def delete(self, path):
            cooked_path = self._cook_path(path)
            if self._is_workspace(cooked_path):
                return "invalid_path"

            if cooked_path.is_file():
                return self.unlink(path)
            else:
                return self.rmdir(path)

        def _delete_stats(self, oldpath):
            new_stats = {}
            for canditate_path, candidate_stat in self.entries_stats.items():
                try:
                    canditate_path.relative_to(oldpath)
                except ValueError:
                    # Candidate is not a child of oldpath
                    new_stats[canditate_path] = candidate_stat
            self.entries_stats = new_stats

        def rename_workspace(self, src, dst):
            src = self._cook_path(src)
            dst = self._cook_path(dst)

            if not self._is_workspace(src) or not self._is_workspace(dst):
                return "invalid_path"

            if dst.exists():
                return "invalid_path"

            try:
                src.rename(str(dst))
            except OSError:
                return "invalid_path"

            if src != dst:
                # Rename all the affected entries
                for child_src, entry in self.entries_stats.copy().items():
                    # Note `child_src` will also contain `src` itself here
                    try:
                        relative = child_src.relative_to(src)
                    except ValueError:
                        continue
                    child_dst = dst / relative
                    self.entries_stats[child_dst] = self.entries_stats.pop(child_src)

                # Remember dst.parent == src.parent == '/'
                self.entries_stats[dst.parent]["need_sync"] = True

            return "ok"

        def move(self, src, dst):
            # TODO: This method should be called rename
            src = self._cook_path(src)
            dst = self._cook_path(dst)

            if self._is_workspace(src) or self._is_workspace(dst):
                return "invalid_path"

            if src.parent != dst.parent:
                return "invalid_path"

            try:
                src.rename(str(dst))
            except OSError:
                return "invalid_path"

            if src != dst:
                # Rename source and all entries within the source
                for child_src, entry in self.entries_stats.copy().items():
                    try:
                        relative = child_src.relative_to(src)
                    except ValueError:
                        continue
                    child_dst = dst / relative
                    entry = self.entries_stats.pop(child_src)
                    self.entries_stats[child_dst] = entry

                # The parent is the only modified entry
                self.entries_stats[src.parent]["need_sync"] = True

            return "ok"

        def sync(self, sync_cb=lambda path, stat: None):
            self._recursive_sync(self.root, sync_cb)
            return "ok"

        def _relative_path(self, path):
            path = str(path.relative_to(self.root))
            return "/" if path == "." else f"/{path}"

        def _recursive_sync(self, path, sync_cb):
            stat = self.entries_stats[path]
            if stat["need_sync"]:
                stat["need_sync"] = False
                stat["is_placeholder"] = False
                stat["base_version"] += 1
                sync_cb(self._relative_path(path), stat)

            if path.is_dir():
                for child in path.iterdir():
                    self._recursive_sync(child, sync_cb)

        def stat(self, path):
            path = self._cook_path(path)
            if path.exists():
                return {"status": "ok", **self.entries_stats[path]}
            else:
                return {"status": "invalid_path"}

    count = 0

    def _oracle_fs_factory():
        nonlocal count
        count += 1
        return OracleFS(Path(tmpdir / f"oracle_fs-{count}"))

    return _oracle_fs_factory


@pytest.fixture
def oracle_fs_with_sync_factory(oracle_fs_factory):
    class OracleFSWithSync:
        def __init__(self):
            self.fs = oracle_fs_factory()
            self.fs.sync()
            self.synced_fs = oracle_fs_factory()
            self.synced_fs.sync()

        def create_file(self, path):
            return self.fs.create_file(path)

        def create_folder(self, path):
            return self.fs.create_folder(path)

        def create_workspace(self, path):
            return self.fs.create_workspace(path)

        def delete(self, path):
            return self.fs.delete(path)

        def rmdir(self, path):
            return self.fs.rmdir(path)

        def unlink(self, path):
            return self.fs.unlink(path)

        def move(self, src, dst):
            return self.fs.move(src, dst)

        def rename_workspace(self, src, dst):
            return self.fs.rename_workspace(src, dst)

        def flush(self, path):
            return self.fs.flush(path)

        def sync(self):
            synced_items = []

            def sync_cb(path, stat):
                synced_items.append((path, stat["base_version"], stat["type"]))

            res = self.fs.sync(sync_cb=sync_cb)
            if res == "ok":
                new_synced = self.fs.copy()

                def _recursive_keep_synced(path):
                    stat = new_synced.entries_stats[path]
                    if stat["type"] in ["folder", "workspace"]:
                        for child in path.iterdir():
                            _recursive_keep_synced(child)
                    stat["need_sync"] = False
                    if stat["is_placeholder"]:
                        del new_synced.entries_stats[path]
                        if stat["type"] == "file":
                            path.unlink()
                        else:
                            path.rmdir()

                _recursive_keep_synced(new_synced.root)
                self.synced_fs = new_synced
            return res

        def stat(self, path):
            return self.fs.stat(path)

        def reset(self):
            self.fs = self.synced_fs.copy()

    def _oracle_fs_with_sync_factory():
        return OracleFSWithSync()

    return _oracle_fs_with_sync_factory


class FileOracle:
    def __init__(self, base_version=0):
        self._buffer = bytearray()
        self._synced_buffer = bytearray()
        self.base_version = base_version
        self.need_sync = base_version == 0

    @property
    def size(self):
        return len(self._buffer)

    def read(self, size, offset):
        return self._buffer[offset : size + offset]

    def write(self, offset, content):
        if not content:
            return

        if offset > len(self._buffer):
            self.truncate(offset + len(content))
        self._buffer[offset : len(content) + offset] = content
        self.need_sync = True

    def truncate(self, length):
        if length == len(self._buffer):
            return
        new_buffer = bytearray(length)
        truncate_length = min(length, len(self._buffer))
        new_buffer[:truncate_length] = self._buffer[:truncate_length]
        self._buffer = new_buffer
        self.need_sync = True

    def sync(self):
        self._synced_buffer = self._buffer.copy()
        if self.need_sync:
            self.base_version += 1
        self.need_sync = False

    def reset(self):
        self._buffer = self._synced_buffer.copy()
        self.need_sync = False
