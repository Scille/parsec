# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pytest

from parsec.core.fs.exceptions import FSWorkspaceTimestampedTooEarly
from parsec.core.types import FsPath


@pytest.mark.trio
async def test_path_info(alice_workspace, timestamp_0, alice_workspace_t1, alice_workspace_t2):
    with pytest.raises(FSWorkspaceTimestampedTooEarly):
        await alice_workspace.to_timestamped(timestamp_0)

    info = await alice_workspace_t1.path_info("/")
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

    info = await alice_workspace_t1.path_info("/foo")
    assert info == {
        "base_version": ANY,
        "children": [],
        "created": ANY,
        "id": ANY,
        "is_placeholder": False,
        "need_sync": False,
        "type": "folder",
        "updated": ANY,
    }

    with pytest.raises(FileNotFoundError):
        info = await alice_workspace_t1.path_info("/foo/bar")

    info = await alice_workspace_t2.path_info("/foo")
    assert info == {
        "base_version": ANY,
        "children": ["bar"],
        "created": ANY,
        "id": ANY,
        "is_placeholder": False,
        "need_sync": False,
        "type": "folder",
        "updated": ANY,
    }

    info = await alice_workspace_t2.path_info("/foo/bar")
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

    with pytest.raises(FileNotFoundError):
        info = await alice_workspace_t2.path_info("/foo/baz")


@pytest.mark.trio
async def test_exists(alice_workspace_t1, alice_workspace_t2, alice_workspace_t3):
    assert await alice_workspace_t1.exists("/") is True
    assert await alice_workspace_t1.exists("/foo") is True

    assert await alice_workspace_t1.exists("/foo/bar") is False
    assert await alice_workspace_t2.exists("/foo/bar") is True

    assert await alice_workspace_t1.exists("/foo/baz") is False
    assert await alice_workspace_t2.exists("/foo/baz") is False
    assert await alice_workspace_t3.exists("/foo/baz") is True


@pytest.mark.trio
async def test_is_dir(alice_workspace_t3):
    assert await alice_workspace_t3.is_dir("/") is True
    assert await alice_workspace_t3.is_dir("/foo") is True
    assert await alice_workspace_t3.is_dir("/foo/bar") is False

    with pytest.raises(FileNotFoundError):
        await alice_workspace_t3.is_dir("/baz")


@pytest.mark.trio
async def test_is_file(alice_workspace_t3):
    assert await alice_workspace_t3.is_file("/") is False
    assert await alice_workspace_t3.is_file("/foo") is False
    assert await alice_workspace_t3.is_file("/foo/bar") is True

    with pytest.raises(FileNotFoundError):
        await alice_workspace_t3.is_file("/baz")


@pytest.mark.trio
async def test_iterdir(alice_workspace_t2, alice_workspace_t3):
    lst = [child async for child in alice_workspace_t2.iterdir("/")]
    assert lst == [FsPath("/foo")]
    lst = [child async for child in alice_workspace_t2.iterdir("/foo")]
    assert lst == [FsPath("/foo/bar")]
    lst = [child async for child in alice_workspace_t3.iterdir("/foo")]
    assert lst == [FsPath("/foo/bar"), FsPath("/foo/baz")]

    with pytest.raises(NotADirectoryError):
        async for child in alice_workspace_t3.iterdir("/foo/bar"):
            assert False, child
    with pytest.raises(FileNotFoundError):
        async for child in alice_workspace_t3.iterdir("/baz"):
            assert False, child


@pytest.mark.trio
async def test_listdir(alice_workspace_t1, alice_workspace_t2, alice_workspace_t3):
    lst = await alice_workspace_t1.listdir("/")
    assert lst == [FsPath("/foo")]
    lst = await alice_workspace_t1.listdir("/foo")
    assert lst == []
    lst = await alice_workspace_t2.listdir("/foo")
    assert lst == [FsPath("/foo/bar")]
    lst = await alice_workspace_t3.listdir("/foo")
    assert lst == [FsPath("/foo/bar"), FsPath("/foo/baz")]

    with pytest.raises(NotADirectoryError):
        await alice_workspace_t3.listdir("/foo/bar")
    with pytest.raises(FileNotFoundError):
        await alice_workspace_t3.listdir("/baz")


@pytest.mark.trio
async def test_rename(alice_workspace_t1):
    with pytest.raises(PermissionError):
        await alice_workspace_t1.rename("/foo", "/foz")


@pytest.mark.trio
async def test_mkdir(alice_workspace_t1):
    with pytest.raises(PermissionError):
        await alice_workspace_t1.mkdir("/foz")


@pytest.mark.trio
async def test_rmdir(alice_workspace_t1):
    with pytest.raises(PermissionError):
        await alice_workspace_t1.rmdir("/foo")


@pytest.mark.trio
async def test_touch(alice_workspace_t1):
    with pytest.raises(PermissionError):
        await alice_workspace_t1.touch("/error")


@pytest.mark.trio
async def test_unlink(alice_workspace_t1):
    with pytest.raises(PermissionError):
        await alice_workspace_t1.unlink("/foo/bar")


@pytest.mark.trio
async def test_truncate(alice_workspace_t4):
    with pytest.raises(PermissionError):
        await alice_workspace_t4.truncate("/files/content", 3)


@pytest.mark.trio
async def test_read_bytes(alice_workspace_t4, alice_workspace_t5):
    assert await alice_workspace_t4.read_bytes("/foo/bar") == b""
    assert await alice_workspace_t4.read_bytes("/files/content") == b"abcde"
    assert await alice_workspace_t5.read_bytes("/files/content") == b"fghij"

    assert await alice_workspace_t4.read_bytes("/files/content", size=3) == b"abc"
    assert await alice_workspace_t4.read_bytes("/files/content", size=2, offset=2) == b"cd"
    assert await alice_workspace_t4.read_bytes("/files/content", size=8, offset=2) == b"cde"

    with pytest.raises(IsADirectoryError):
        await alice_workspace_t4.read_bytes("/foo")
    with pytest.raises(IsADirectoryError):
        await alice_workspace_t4.read_bytes("/", 0)


@pytest.mark.trio
async def test_write_bytes(alice_workspace_t4):
    with pytest.raises(PermissionError):
        await alice_workspace_t4.write_bytes("/foo/bar", b"abcde")

    with pytest.raises(PermissionError):
        await alice_workspace_t4.write_bytes("/foo", b"abcde")
    with pytest.raises(PermissionError):
        await alice_workspace_t4.write_bytes("/", b"abcde")


@pytest.mark.trio
async def test_move(alice_workspace_t3):
    with pytest.raises(PermissionError):
        await alice_workspace_t3.move("/foo", "/foz")
    with pytest.raises(PermissionError):
        await alice_workspace_t3.move("/foo/bar", "/foo/bal")


@pytest.mark.trio
async def test_copytree(alice_workspace_t3):
    with pytest.raises(PermissionError):
        await alice_workspace_t3.copytree("/foo", "/cfoo")


@pytest.mark.trio
async def test_copyfile(alice_workspace_t3):
    with pytest.raises(PermissionError):
        await alice_workspace_t3.copyfile("/foo/bar", "/copied")


@pytest.mark.trio
async def test_rmtree(alice_workspace_t3):
    with pytest.raises(PermissionError):
        await alice_workspace_t3.rmtree("/foo")
