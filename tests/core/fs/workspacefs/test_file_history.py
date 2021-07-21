# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

import pytest

from parsec.core.fs.workspacefs.versioning_helpers import VersionLister, TimestampBoundedData
from parsec.core.types import FsPath
from unittest.mock import ANY


@pytest.mark.trio
async def test_file_history(alice, alice_workspace):
    sync_by_id = alice_workspace.sync_by_id
    entry = alice_workspace.get_workspace_entry()
    wid = entry.id

    # Creating files
    await alice_workspace.touch("/f")

    # Test version before first sync
    versions_list, download_limit_reached = await VersionLister(alice_workspace).list(FsPath("/f"))
    assert download_limit_reached is True
    assert not versions_list

    await sync_by_id(wid)

    # Checking again, should have the first version now
    versions_list, download_limit_reached = await VersionLister(alice_workspace).list(FsPath("/f"))
    assert download_limit_reached is True
    assert versions_list == [
        TimestampBoundedData(
            id=ANY,
            version=1,
            early=ANY,
            late=ANY,
            creator=alice.device_id,
            updated=ANY,
            is_folder=False,
            size=0,
            source=None,
            destination=None,
        )
    ]

    import time

    # Updating the file a couple of time and sync again to test the version list
    before_loop = time.time()
    for i in range(200):
        f = await alice_workspace.open_file("/f", "ab")
        await f.write(str(i).encode())
        await f.close()
        await sync_by_id(wid)
    total_time = time.time() - before_loop
    print("loop time = ", total_time)
    before_versions_list = time.time()
    versions_list, download_limit_reached = await VersionLister(alice_workspace).list(FsPath("/f"))
    total_time = time.time() - before_versions_list
    print("versions_list time = ", total_time)
    assert download_limit_reached is True

    # _sanitize_list is removing the first 1 version because it is the empty manifest set
    version_nb = 2
    previous_late = None
    for version in versions_list:
        assert version.version == version_nb
        version_nb += 1
        # Checking if DateTime is coherent
        if previous_late:
            assert previous_late == version.early
        previous_late = version.late
    # File should have 21 versions (20 modifications + 1 creation). version_nb - 1 because it is
    # incremented once too often at the last loop cycle.
    # assert version_nb - 1 == 21
