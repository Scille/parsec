# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from functools import partial

from parsec.core.fs.workspacefs.versioning_helpers import VersionLister
from parsec.core.types import FsPath


@pytest.mark.trio
async def test_files_history(alice_workspace):
    sync_by_id = partial(alice_workspace.sync_by_id, remote_changed=True)
    entry = alice_workspace.get_workspace_entry()
    wid = entry.id

    # Empty workspace
    await sync_by_id(wid)

    # Creating files and writing first byte
    await alice_workspace.touch("/f")
    await alice_workspace.touch("/f2")

    # Test version before first sync
    f_versions = await VersionLister(alice_workspace).list(FsPath("/f"))

    # Assert if task list have been completed
    assert f_versions[1] is True

    # Should have no version yet because we didn't sync
    assert not f_versions[0]

    # Syncronysing the files
    await sync_by_id(wid)

    # Checking again, should have the first version now
    f_versions = await VersionLister(alice_workspace).list(FsPath("/f"))

    # Assert if task list have been completed
    assert f_versions[1] is True
    # Assert there is a version in the list
    assert f_versions[0]
    # Checking the first version of the list
    assert getattr(f_versions[0][0], "version") == 1

    f2_versions = await VersionLister(alice_workspace).list(FsPath("/f2"))

    # Assert if task list have been completed
    assert f2_versions[1] is True
    # Assert there is a version in the list
    assert f2_versions[0]
    # Checking the first version of the list
    assert getattr(f2_versions[0][0], "version") == 1

    # Updating the file a couple of time and sync again to test the version list
    for i in range(20):
        f = await alice_workspace.open_file("/f", "ab")
        await f.write(str(i).encode())
        await f.close()
        await sync_by_id(wid)
    f_versions = await VersionLister(alice_workspace).list(FsPath("/f"))

    # _sanitize_list is removing the first 1 version because it is the empty manifest set
    version_nb = 2
    previous_late = None
    for version in f_versions[0]:
        assert version.version == version_nb
        version_nb += 1
        if previous_late:
            assert previous_late == version.early
        previous_late = version.late
