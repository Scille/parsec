# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum

from parsec.api.data import UserManifest, WorkspaceEntry
from parsec.core.fs.userfs.merging import merge_local_user_manifests
from parsec.core.types import LocalUserManifest


@pytest.fixture
def gen_date():
    curr = Pendulum(2000, 1, 1)

    def _gen():
        nonlocal curr
        while True:
            yield curr
            curr = curr.add(days=1)

    g = _gen()
    return lambda: next(g)


@pytest.mark.parametrize("with_ignored_changes", (False, True))
def test_merge_local_user_manifest_no_changes_in_diverged_placeholder(
    gen_date, alice, alice2, with_ignored_changes
):
    w1 = WorkspaceEntry.new(name="w1")
    d1, d2, d3, d4 = [gen_date() for _ in range(4)]

    base = UserManifest(
        author=alice.device_id,
        timestamp=d2,
        id=alice.user_manifest_id,
        version=1,
        created=d1,
        updated=d2,
        last_processed_message=0,
        workspaces=(w1,),
    )

    diverged = LocalUserManifest.from_remote(base)
    if with_ignored_changes:
        diverged = diverged.evolve(updated=d4, need_sync=True)

    target = UserManifest(
        author=alice2.device_id,
        timestamp=d2,
        id=alice2.user_manifest_id,
        version=2,
        created=d1,
        updated=d3,
        last_processed_message=0,
        workspaces=(w1,),
    )

    expected_merged = LocalUserManifest.from_remote(target)
    merged = merge_local_user_manifests(diverged, target)
    assert merged == expected_merged


def test_merge_local_user_manifest_changes_placeholder(gen_date, alice):
    d1, d2, d3, d4 = [gen_date() for _ in range(4)]
    w1 = WorkspaceEntry.new(name="w1")
    w2 = WorkspaceEntry.new(name="w2")
    w3 = WorkspaceEntry.new(name="w3")

    diverged = LocalUserManifest.new_placeholder(id=alice.user_manifest_id, now=d4).evolve(
        last_processed_message=30, workspaces=(w1, w3)
    )
    target = UserManifest(
        author=alice.device_id,
        timestamp=d2,
        id=alice.user_manifest_id,
        version=3,
        created=d1,
        updated=d3,
        last_processed_message=20,
        workspaces=(w1, w2),
    )
    expected_merged = LocalUserManifest(
        base=target, updated=d4, last_processed_message=30, workspaces=(w1, w2, w3), need_sync=True
    )

    merged = merge_local_user_manifests(diverged, target)
    assert merged == expected_merged
