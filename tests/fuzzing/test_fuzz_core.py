# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from collections import defaultdict
from random import choice, randrange
from string import ascii_lowercase
from time import monotonic

import attr
import pytest
import trio

from parsec.core.fs import FSError
from parsec.core.types import WorkspaceRole

FUZZ_PARALLELISM = 10
FUZZ_TIME = 10.0


def generate_name():
    return "".join([choice(ascii_lowercase) for x in range(4)])


@attr.s
class FSState:
    stats = defaultdict(lambda: defaultdict(lambda: 0))
    logs = attr.ib(factory=list)
    files = attr.ib(factory=list)
    folders = attr.ib(factory=lambda: ["/"])

    def get_cooked_stats(self):
        stats = {}
        for fuzzer_stat in self.stats.values():
            for k, v in fuzzer_stat.items():
                try:
                    stats[k] += v
                except KeyError:
                    stats[k] = v
        total = sum(stats.values())
        return sorted([(k, (v * 100 // total)) for k, v in stats.items()], key=lambda x: -x[1])

    def get_tree(self):
        def is_direct_child_of(parent, candidate):
            if candidate.startswith(parent):
                relative_candidate = candidate[len(parent) :]
                if relative_candidate.startswith("/"):
                    relative_candidate = relative_candidate[1:]
                if relative_candidate and "/" not in relative_candidate:
                    return relative_candidate
            return None

        def recursive_build_tree(path):
            tree = {}

            for candidate_child in self.folders:
                candidate_relative_path = is_direct_child_of(path, candidate_child)
                if candidate_relative_path:
                    tree[candidate_relative_path] = recursive_build_tree(candidate_child)

            for candidate_child in self.files:
                candidate_relative_path = is_direct_child_of(path, candidate_child)
                if candidate_relative_path:
                    tree[candidate_relative_path] = "<File>"

            return tree

        return recursive_build_tree("/")

    def add_stat(self, fuzzer_id, type, log):
        self.stats[fuzzer_id][type] += 1
        self.logs.append((monotonic(), fuzzer_id, type, log))

    def format_logs(self):
        logs = []
        for ts, fuzzer_id, type, log in self.logs:
            logs.append(f"{ts}[{fuzzer_id}]:{type}:{log}")
        return "\n".join(logs)

    def get_folder(self):
        return self.folders[randrange(0, len(self.folders))].replace("//", "/")

    def get_file(self):
        if not self.files:
            raise SkipCommand()
        return self.files[randrange(0, len(self.files))].replace("//", "/")

    def get_path(self):
        if self.files and randrange(0, 2):
            return self.get_file()
        else:
            return self.get_folder()

    def remove_path(self, path):
        try:
            self.files.remove(path)
        except ValueError:
            if path == "/":
                self.folders = {}
                self.files = {}
            else:
                if not path.endswith("/"):
                    path += "/"
                self.folders = [x for x in self.folders if not x.startswith(path)]
                self.files = [x for x in self.files if not x.startswith(path)]

    def replace_path(self, old_path, new_path):
        try:
            self.files.remove(old_path)
            self.files.append(new_path)
        except ValueError:
            self.folders.remove(old_path)
            self.folders.append(new_path)

    def get_new_path(self):
        return ("%s/%s" % (self.get_folder(), generate_name())).replace("//", "/")


class SkipCommand(Exception):
    pass


async def fuzzer(id, core, workspace, fs_state):
    while True:
        try:
            await _fuzzer_cmd(id, core, workspace, fs_state)
        except SkipCommand:
            fs_state.add_stat(id, "skipped command", "...")


async def _fuzzer_cmd(id, core, workspace, fs_state):
    x = randrange(0, 100)
    await trio.sleep(x * 0.01)

    if x < 10:
        path = fs_state.get_path()
        try:
            stat = await workspace.path_info(path)
            fs_state.add_stat(id, "stat_ok", f"path={path}, returned stats={stat!r}")
        except OSError as exc:
            fs_state.add_stat(id, "stat_bad", f"path={path}, raised {exc!r}")

    elif x < 20:
        path = fs_state.get_new_path()
        try:
            await workspace.touch(path)
            fs_state.files.append(path)
            fs_state.add_stat(id, "file_create_ok", f"path={path}")
        except OSError as exc:
            fs_state.add_stat(id, "file_create_bad", f"path={path}, raised {exc!r}")

    elif x < 30:
        path = fs_state.get_file()
        size = randrange(0, 2000)
        try:
            ret = await workspace.read_bytes(path, size=size)
            fs_state.add_stat(
                id, "file_read_ok", f"path={path}, size={size}, returned {len(ret)} bytes"
            )
        except OSError as exc:
            fs_state.add_stat(id, "file_read_bad", f"path={path}, size={size}, raised {exc!r}")

    elif x < 40:
        path = fs_state.get_file()
        buffer = b"x" * randrange(1, 1000)
        offset = randrange(0, 100)
        try:
            await workspace.write_bytes(path, buffer, offset=offset)
            fs_state.add_stat(
                id, "file_write_ok", f"path={path}, buffer size={len(buffer)}, offset={offset}"
            )
        except OSError as exc:
            fs_state.add_stat(
                id,
                "file_write_bad",
                f"path={path}, buffer size={len(buffer)}, offset={offset}, raised {exc!r}",
            )

    elif x < 50:
        path = fs_state.get_file()
        length = randrange(0, 100)
        try:
            await workspace.truncate(path, length)
            fs_state.add_stat(id, "file_truncate_ok", f"path={path}, length={length}")
        except OSError as exc:
            fs_state.add_stat(
                id, "file_truncate_bad", f"path={path}, length={length}, raised {exc!r}"
            )

    elif x < 60:
        path = fs_state.get_new_path()
        try:
            await workspace.mkdir(path)
            fs_state.folders.append(path)
            fs_state.add_stat(id, "folder_create_ok", f"path={path}")
        except OSError as exc:
            fs_state.add_stat(id, "folder_create_bad", f"path={path}, raised {exc!r}")

    elif x < 70:
        old_path = fs_state.get_path()
        new_path = fs_state.get_new_path()
        try:
            await workspace.rename(old_path, new_path)
            fs_state.replace_path(old_path, new_path)
            fs_state.add_stat(id, "move_ok", f"src={old_path}, dst={new_path}")
        except OSError as exc:
            fs_state.add_stat(id, "move_bad", f"src={old_path}, dst={new_path}, raised {exc!r}")

    elif x < 80:
        try:
            if x < 85:
                path = fs_state.get_file()
                await workspace.unlink(path)
            else:
                path = fs_state.get_folder()
                await workspace.rmdir(path)
            fs_state.remove_path(path)
            fs_state.add_stat(id, "delete_ok", f"path={path}")
        except OSError as exc:
            fs_state.add_stat(id, "delete_bad", f"path={path}, raised {exc!r}")

    elif x < 90:
        path = fs_state.get_path()
        try:
            entry_id = await workspace.path_id(path)
            await workspace.sync_by_id(entry_id)
            fs_state.add_stat(id, "sync_ok", f"path={path}")
        except OSError as exc:
            fs_state.add_stat(id, "sync_bad", f"path={path}, raised {exc!r}")

    else:
        path = fs_state.get_path()
        try:
            await core.user_fs.workspace_share(path[1:], "bob", WorkspaceRole.OWNER)
            fs_state.add_stat(id, "share_ok", f"path={path}")
        except FSError as exc:
            fs_state.add_stat(id, "share_bad", f"path={path}, raised {exc!r}")


@pytest.mark.trio
@pytest.mark.slow
async def test_fuzz_core(request, running_backend, alice_core):
    await trio.sleep(0.1)  # Somehow fixes the test
    wid = await alice_core.user_fs.workspace_create("w")
    workspace = alice_core.user_fs.get_workspace(wid)
    try:
        async with trio.open_service_nursery() as nursery:
            fs_state = FSState()
            for i in range(FUZZ_PARALLELISM):
                nursery.start_soon(fuzzer, i, alice_core, workspace, fs_state)
            await trio.sleep(FUZZ_TIME)
            nursery.cancel_scope.cancel()

    except BaseException:
        print(fs_state.format_logs())
        raise

    finally:

        def prettify(tree, indent=0):
            for key, value in tree.items():
                if isinstance(value, dict):
                    print("  " * indent + key + " <Folder>")
                    prettify(value, indent + 1)
                else:
                    print("  " * indent + key + " <File>")

        print("Final fs tree generated:")
        print("/")
        prettify(fs_state.get_tree(), 1)
        print()
        print("Stats:")
        for k, v in fs_state.get_cooked_stats():
            print(" - %s: %s%%" % (k, v))
