# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import errno
from unittest.mock import ANY

import pytest

from parsec.api.data import Manifest as RemoteManifest
from parsec.api.protocol import DeviceID, RealmRole
from parsec.core.fs.exceptions import FSError
from parsec.core.types import EntryID, FsPath


@pytest.fixture
@pytest.mark.trio
async def alice_workspace(alice_user_fs, running_backend):
    wid = await alice_user_fs.workspace_create("w")
    workspace = alice_user_fs.get_workspace(wid)
    await workspace.mkdir("/foo")
    await workspace.touch("/foo/bar")
    await workspace.touch("/foo/baz")
    await workspace.sync()
    return workspace


@pytest.mark.trio
async def test_workspace_properties(alice_workspace):
    assert alice_workspace.get_workspace_name() == "w"
    assert alice_workspace.get_encryption_revision() == 1


@pytest.mark.trio
async def test_path_info(alice_workspace):
    info = await alice_workspace.path_info("/")
    assert info == {
        "base_version": ANY,
        "children": ["foo"],
        "created": ANY,
        "id": ANY,
        "is_placeholder": False,
        "need_sync": False,
        "type": "folder",
        "updated": ANY,
    }

    info = await alice_workspace.path_info("/foo")
    assert info == {
        "base_version": ANY,
        "children": ["bar", "baz"],
        "created": ANY,
        "id": ANY,
        "is_placeholder": False,
        "need_sync": False,
        "type": "folder",
        "updated": ANY,
    }

    info = await alice_workspace.path_info("/foo/bar")
    assert info == {
        "base_version": ANY,
        "size": 0,
        "created": ANY,
        "id": ANY,
        "is_placeholder": False,
        "need_sync": False,
        "type": "file",
        "updated": ANY,
    }


@pytest.mark.trio
async def test_get_user_roles(alice_workspace):
    assert await alice_workspace.get_user_roles() == {
        alice_workspace.device.user_id: RealmRole.OWNER
    }


@pytest.mark.trio
async def test_exists(alice_workspace):
    assert await alice_workspace.exists("/") is True
    assert await alice_workspace.exists("/foo") is True
    assert await alice_workspace.exists("/foo/bar") is True
    assert await alice_workspace.exists("/foo/baz") is True

    assert await alice_workspace.exists("/fiz") is False
    assert await alice_workspace.exists("/foo/fiz") is False
    assert await alice_workspace.exists("/fiz/foo") is False
    assert await alice_workspace.exists("/foo/bar/baz") is False


@pytest.mark.trio
async def test_is_dir(alice_workspace):
    assert await alice_workspace.is_dir("/") is True
    assert await alice_workspace.is_dir("/foo") is True
    assert await alice_workspace.is_dir("/foo/bar") is False

    with pytest.raises(FileNotFoundError):
        await alice_workspace.is_dir("/baz")


@pytest.mark.trio
async def test_is_file(alice_workspace):
    assert await alice_workspace.is_file("/") is False
    assert await alice_workspace.is_file("/foo") is False
    assert await alice_workspace.is_file("/foo/bar") is True

    with pytest.raises(FileNotFoundError):
        await alice_workspace.is_file("/baz")


@pytest.mark.trio
async def test_iterdir(alice_workspace):
    lst = [child async for child in alice_workspace.iterdir("/")]
    assert lst == [FsPath("/foo")]
    lst = [child async for child in alice_workspace.iterdir("/foo")]
    assert lst == [FsPath("/foo/bar"), FsPath("/foo/baz")]

    with pytest.raises(NotADirectoryError):
        async for child in alice_workspace.iterdir("/foo/bar"):
            assert False, child
    with pytest.raises(FileNotFoundError):
        async for child in alice_workspace.iterdir("/baz"):
            assert False, child


@pytest.mark.trio
async def test_listdir(alice_workspace):
    lst = await alice_workspace.listdir("/")
    assert lst == [FsPath("/foo")]
    lst = await alice_workspace.listdir("/foo")
    assert lst == [FsPath("/foo/bar"), FsPath("/foo/baz")]

    with pytest.raises(NotADirectoryError):
        await alice_workspace.listdir("/foo/bar")
    with pytest.raises(FileNotFoundError):
        await alice_workspace.listdir("/baz")


@pytest.mark.trio
async def test_rename(alice_workspace):
    await alice_workspace.rename("/foo", "/foz")
    await alice_workspace.rename("/foz/bar", "/foz/bal")
    assert await alice_workspace.is_file("/foz/bal")

    with pytest.raises(OSError) as context:
        await alice_workspace.rename("/foz/baz", "/baz")
    assert context.value.errno == errno.EXDEV
    with pytest.raises(FileNotFoundError):
        await alice_workspace.rename("/foo", "/fob")


@pytest.mark.trio
async def test_mkdir(alice_workspace):
    await alice_workspace.mkdir("/foz")
    assert await alice_workspace.is_dir("/foz")

    await alice_workspace.mkdir("/a/b/c/d", parents=True)
    lst = await alice_workspace.listdir("/a/b/c")
    assert lst == [FsPath("/a/b/c/d")]

    with pytest.raises(FileNotFoundError):
        await alice_workspace.mkdir("/x/y/z")

    await alice_workspace.mkdir("/foo", exist_ok=True)
    with pytest.raises(FileExistsError):
        await alice_workspace.mkdir("/foo")
    with pytest.raises(FileExistsError):
        await alice_workspace.mkdir("/foo/bar")


@pytest.mark.trio
async def test_rmdir(alice_workspace):
    await alice_workspace.mkdir("/foz")
    await alice_workspace.rmdir("/foz")
    lst = await alice_workspace.listdir("/")
    assert lst == [FsPath("/foo")]

    with pytest.raises(OSError) as context:
        await alice_workspace.rmdir("/foo")
    assert context.value.errno == errno.ENOTEMPTY

    with pytest.raises(NotADirectoryError):
        await alice_workspace.rmdir("/foo/bar")

    with pytest.raises(PermissionError):
        await alice_workspace.rmdir("/")


@pytest.mark.trio
async def test_touch(alice_workspace):
    await alice_workspace.touch("/bar")
    assert await alice_workspace.is_file("/bar")
    info = await alice_workspace.path_info("/bar")
    assert info["size"] == 0

    await alice_workspace.touch("/bar")
    assert await alice_workspace.is_file("/bar")
    info = await alice_workspace.path_info("/bar")
    assert info["size"] == 0

    with pytest.raises(FileExistsError):
        await alice_workspace.touch("/bar", exist_ok=False)

    await alice_workspace.touch("/foo")


@pytest.mark.trio
async def test_unlink(alice_workspace):
    await alice_workspace.unlink("/foo/bar")
    lst = await alice_workspace.listdir("/foo")
    assert lst == [FsPath("/foo/baz")]

    with pytest.raises(FileNotFoundError):
        await alice_workspace.unlink("/foo/bar")
    with pytest.raises(IsADirectoryError):
        await alice_workspace.unlink("/foo")
    # TODO: should this be a `IsADirectoryError`?
    with pytest.raises(PermissionError):
        await alice_workspace.unlink("/")


@pytest.mark.trio
async def test_truncate(alice_workspace):
    await alice_workspace.write_bytes("/foo/bar", b"abcde")
    await alice_workspace.truncate("/foo/bar", 3)
    assert await alice_workspace.read_bytes("/foo/bar") == b"abc"

    await alice_workspace.truncate("/foo/bar", 13)
    assert await alice_workspace.read_bytes("/foo/bar") == b"abc" + b"\x00" * 10

    await alice_workspace.truncate("/foo/bar", 0)
    assert await alice_workspace.read_bytes("/foo/bar") == b""

    with pytest.raises(IsADirectoryError):
        await alice_workspace.truncate("/foo", 0)
    with pytest.raises(IsADirectoryError):
        await alice_workspace.truncate("/", 0)


@pytest.mark.trio
async def test_read_bytes(alice_workspace):
    assert await alice_workspace.read_bytes("/foo/bar") == b""

    await alice_workspace.write_bytes("/foo/bar", b"abcde")
    assert await alice_workspace.read_bytes("/foo/bar", size=3) == b"abc"
    assert await alice_workspace.read_bytes("/foo/bar", size=2, offset=2) == b"cd"
    assert await alice_workspace.read_bytes("/foo/bar", size=8, offset=2) == b"cde"

    with pytest.raises(IsADirectoryError):
        await alice_workspace.read_bytes("/foo")
    with pytest.raises(IsADirectoryError):
        await alice_workspace.read_bytes("/", 0)


@pytest.mark.trio
async def test_write_bytes(alice_workspace):
    # Pathlib mode (truncate=True)
    await alice_workspace.write_bytes("/foo/bar", b"abcde")
    assert await alice_workspace.read_bytes("/foo/bar") == b"abcde"
    await alice_workspace.write_bytes("/foo/bar", b"xyz", offset=1)
    assert await alice_workspace.read_bytes("/foo/bar") == b"axyz"
    await alice_workspace.write_bytes("/foo/bar", b"[append]", offset=-1)
    assert await alice_workspace.read_bytes("/foo/bar") == b"axyz[append]"

    # Clear the content of an existing file
    await alice_workspace.write_bytes("/foo/bar", b"")
    assert await alice_workspace.read_bytes("/foo/bar") == b""

    # Atomic write mode (truncate=False)
    assert await alice_workspace.write_bytes("/foo/bar", b"abcde", truncate=False) == 5
    assert await alice_workspace.read_bytes("/foo/bar") == b"abcde"
    assert await alice_workspace.write_bytes("/foo/bar", b"xyz", offset=1, truncate=False) == 3
    assert await alice_workspace.read_bytes("/foo/bar") == b"axyze"
    assert (
        await alice_workspace.write_bytes("/foo/bar", b"[append]", offset=-1, truncate=False) == 8
    )
    assert await alice_workspace.read_bytes("/foo/bar") == b"axyze[append]"

    with pytest.raises(IsADirectoryError):
        await alice_workspace.read_bytes("/foo")
    with pytest.raises(IsADirectoryError):
        await alice_workspace.read_bytes("/", 0)


@pytest.mark.trio
async def test_move(alice_workspace):
    await alice_workspace.move("/foo", "/foz")
    await alice_workspace.move("/foz/bar", "/foz/bal")
    assert await alice_workspace.is_file("/foz/bal")

    with pytest.raises(FileNotFoundError):
        await alice_workspace.move("/foo", "/fob")

    await alice_workspace.write_bytes("/foz/bal", b"abcde")
    await alice_workspace.write_bytes("/foz/baz", b"fghij")
    await alice_workspace.mkdir("/containfoz")
    await alice_workspace.move("/foz", "/containfoz")
    assert await alice_workspace.is_file("/containfoz/foz/bal")
    assert await alice_workspace.is_file("/containfoz/foz/baz")
    assert await alice_workspace.read_bytes("/containfoz/foz/bal", size=5) == b"abcde"
    assert await alice_workspace.read_bytes("/containfoz/foz/baz", size=5) == b"fghij"

    with pytest.raises(FileNotFoundError):
        await alice_workspace.move("/foz/baz", "/baz")

    await alice_workspace.move("/containfoz/foz/baz", "/baz")
    assert await alice_workspace.is_file("/baz")

    with pytest.raises(FileNotFoundError):
        await alice_workspace.move("/containfoz/foz/baz", "/biz")

    with pytest.raises(FileExistsError):
        await alice_workspace.move("/containfoz/foz/bal", "/baz")

    with pytest.raises(OSError):
        await alice_workspace.move("/containfoz", "/containfoz/foz")

    with pytest.raises(FileExistsError):
        await alice_workspace.move("/containfoz/foz", "/containfoz")


@pytest.mark.trio
async def test_copytree(alice_workspace):
    await alice_workspace.write_bytes("/foo/bar", b"a" * 9000 + b"b" * 40000)
    await alice_workspace.write_bytes("/foo/baz", b"a" * 40000 + b"b" * 9000)
    await alice_workspace.mkdir("/foo/dir")
    await alice_workspace.touch("/foo/dir/bar")
    await alice_workspace.write_bytes("/foo/dir/bar", b"a" * 5000 + b"b" * 6000)

    await alice_workspace.copytree("/foo", "/cfoo")
    assert await alice_workspace.read_bytes("/foo/bar") == b"a" * 9000 + b"b" * 40000
    assert await alice_workspace.read_bytes("/foo/baz") == b"a" * 40000 + b"b" * 9000
    assert await alice_workspace.read_bytes("/foo/dir/bar") == b"a" * 5000 + b"b" * 6000


@pytest.mark.trio
async def test_copyfile(alice_workspace):
    await alice_workspace.write_bytes("/foo/bar", b"a" * 9000 + b"b" * 40000)
    await alice_workspace.copyfile("/foo/bar", "/copied")
    assert await alice_workspace.read_bytes("/copied") == b"a" * 9000 + b"b" * 40000


@pytest.mark.trio
async def test_rmtree(alice_workspace):
    await alice_workspace.mkdir("/foz")
    await alice_workspace.rmtree("/foz")
    lst = await alice_workspace.listdir("/")
    assert lst == [FsPath("/foo")]

    with pytest.raises(NotADirectoryError):
        await alice_workspace.rmdir("/foo/bar")

    await alice_workspace.mkdir("/foo/foz")
    await alice_workspace.touch("/foo/foz/faz")

    await alice_workspace.rmtree("/foo")
    lst = await alice_workspace.listdir("/")
    assert lst == []

    with pytest.raises(PermissionError):
        await alice_workspace.rmtree("/")


@pytest.mark.trio
async def test_dump(alice_workspace):
    baz_id = await alice_workspace.path_id("/foo/baz")
    async with alice_workspace.local_storage.lock_entry_id(baz_id):
        await alice_workspace.local_storage.clear_manifest(baz_id)
    assert await alice_workspace.dump() == {
        "base_version": 1,
        "children": {
            "foo": {
                "base_version": 2,
                "children": {
                    "bar": {
                        "base_version": 1,
                        "blocksize": 524_288,
                        "blocks": [],
                        "created": ANY,
                        "id": ANY,
                        "is_placeholder": False,
                        "need_sync": False,
                        "parent": ANY,
                        "size": 0,
                        "updated": ANY,
                    },
                    "baz": {"id": ANY},
                },
                "created": ANY,
                "id": ANY,
                "is_placeholder": False,
                "need_sync": False,
                "parent": ANY,
                "updated": ANY,
            }
        },
        "created": ANY,
        "id": ANY,
        "is_placeholder": False,
        "need_sync": False,
        "updated": ANY,
    }


@pytest.mark.trio
async def test_path_info_remote_loader_exceptions(monkeypatch, alice_workspace, alice):
    manifest = await alice_workspace.transactions._get_manifest_from_path(FsPath("/foo/bar"))
    async with alice_workspace.local_storage.lock_entry_id(manifest.id):
        await alice_workspace.local_storage.clear_manifest(manifest.id)

    vanilla_file_manifest_deserialize = RemoteManifest._deserialize

    def mocked_file_manifest_deserialize(*args, **kwargs):
        return vanilla_file_manifest_deserialize(*args, **kwargs).evolve(**manifest_modifiers)

    monkeypatch.setattr(RemoteManifest, "_deserialize", mocked_file_manifest_deserialize)

    manifest_modifiers = {"id": EntryID()}
    with pytest.raises(FSError) as exc:
        await alice_workspace.path_info(FsPath("/foo/bar"))
    assert f"Invalid entry ID: expected `{manifest.id}`, got `{manifest_modifiers['id']}`" in str(
        exc.value
    )

    manifest_modifiers = {"version": 4}
    with pytest.raises(FSError) as exc:
        await alice_workspace.path_info(FsPath("/foo/bar"))
    assert "Invalid version: expected `1`, got `4`" in str(exc.value)

    manifest_modifiers = {"author": DeviceID("mallory@pc1")}
    with pytest.raises(FSError) as exc:
        await alice_workspace.path_info(FsPath("/foo/bar"))
    assert "Invalid author: expected `alice@dev1`, got `mallory@pc1`" in str(exc.value)
