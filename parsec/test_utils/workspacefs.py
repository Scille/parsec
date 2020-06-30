# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import string

from parsec.core.fs import FSFileNotFoundError, UserFS, WorkspaceFS
from parsec.core.types import EntryID, FsPath


async def make_workspace_dir_inconsistent(workspace: WorkspaceFS, dir: FsPath):
    await workspace.mkdir(dir)
    await workspace.touch(dir / "foo.txt")
    rep_info = await workspace.transactions.entry_info(dir)
    rep_manifest = await workspace.local_storage.get_manifest(rep_info["id"])
    children = rep_manifest.children
    children["newfail.txt"] = EntryID("b9295787-d9aa-6cbd-be27-1ff83ac72fa6")
    rep_manifest.evolve(children=children)
    async with workspace.local_storage.lock_manifest(rep_info["id"]):
        await workspace.local_storage.set_manifest(rep_info["id"], rep_manifest)
    await workspace.sync()


async def make_workspace_dir_simple_versions(workspace: WorkspaceFS, dir: FsPath):
    await workspace.mkdir(dir)
    await workspace.sync()
    for i in range(0, 9):
        file_name = f"hello.txt"
        path = f"{dir}/{file_name}"
        num_chars = (i % 4 + 4) * 100 * 1024
        text = "".join(
            [
                string.ascii_letters[(i + 1) * (j + 1) % len(string.ascii_letters)]
                for j in range(num_chars)
            ]
        )
        try:
            await workspace.write_bytes(path, text.encode())
        except FSFileNotFoundError:
            await workspace.touch(path)
            await workspace.write_bytes(path, text.encode())
        await workspace.sync()


async def make_workspace_dir_complex_versions(workspace: WorkspaceFS, dir: FsPath):
    await workspace.mkdir(dir)
    await workspace.sync()
    # Create useless stuff before file creation.
    for i in range(50):
        await workspace.touch(dir / f"foo{i}.txt")
        await workspace.sync()
    await workspace.mkdir(dir / "foo")
    await workspace.sync()
    for i in range(50):
        await workspace.touch(dir / "foo" / f"foo{i}.txt")
        await workspace.sync()
    await workspace.mkdir(dir / "foo" / "foo")
    await workspace.sync()
    # Now write an interesting file when benchmarking GUI
    await workspace.touch(dir / "foo" / "foo" / "foo.txt")
    await workspace.sync()
    # Add files in the same directory
    for i in range(50):
        await workspace.touch(dir / "foo" / "foo" / f"foo{i}.txt")
        await workspace.sync()
    # Remove useless stuff after file creation.
    for i in range(50):
        await workspace.unlink(dir / "foo" / "foo" / f"foo{i}.txt")
        await workspace.unlink(dir / "foo" / f"foo{i}.txt")
        await workspace.unlink(dir / f"foo{i}.txt")
        await workspace.sync()


async def create_inconsistent_workspace(user_fs: UserFS, name="w") -> WorkspaceFS:
    wid = await user_fs.workspace_create(name)
    workspace = user_fs.get_workspace(wid)
    await make_workspace_dir_inconsistent(workspace, FsPath("/rep"))
    return workspace
