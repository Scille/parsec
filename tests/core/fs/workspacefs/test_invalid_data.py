# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from pendulum import Pendulum

from parsec.core.fs import FSError
from parsec.core.types import WorkspaceRole
from tests.common import freeze_time


@pytest.fixture
async def testbed(running_backend, alice_user_fs, alice, bob):
    with freeze_time("2000-01-01"):
        wid = await alice_user_fs.workspace_create("w1")
        workspace = alice_user_fs.get_workspace(wid)
        await workspace.sync()
        local_manifest = await workspace.local_storage.get_manifest(wid)
    with freeze_time("2000-01-03"):
        await alice_user_fs.workspace_share(wid, bob.user_id, WorkspaceRole.MANAGER)

    class TestBed:
        def __init__(self):
            self._next_version = 2
            self.defaults = {
                "local_manifest": local_manifest,
                "blob": None,
                "signed_author": alice.device_id,
                "backend_author": alice.device_id,
                "signed_timestamp": Pendulum(2000, 1, 2),
                "backend_timestamp": Pendulum(2000, 1, 2),
                "author_signkey": alice.signing_key,
                "key": workspace.get_workspace_entry().key,
            }

        async def run(self, exc_msg, **kwargs):
            options = {**self.defaults, **kwargs}

            if options["blob"] is None:
                to_sync_um = options["local_manifest"].to_remote(
                    author=options["signed_author"], timestamp=options["signed_timestamp"]
                )
                options["blob"] = to_sync_um.dump_sign_and_encrypt(
                    author_signkey=options["author_signkey"], key=options["key"]
                )

            await running_backend.backend.vlob.update(
                organization_id=alice.organization_id,
                author=options["backend_author"],
                encryption_revision=1,
                vlob_id=wid,
                version=self._next_version,
                timestamp=options["backend_timestamp"],
                blob=options["blob"],
            )
            self._next_version += 1

            # This should trigger FSError
            with pytest.raises(FSError) as exc:
                await workspace.sync()
            assert str(exc.value) == exc_msg

            # Also test timestamped workspace
            with pytest.raises(FSError) as exc:
                await workspace.to_timestamped(options["backend_timestamp"])
            assert str(exc.value) == exc_msg

    return TestBed()


@pytest.mark.trio
async def test_empty_blob(testbed):
    exc_msg = "Cannot decrypt vlob: The nonce must be exactly 24 bytes long"
    await testbed.run(blob=b"", exc_msg=exc_msg)


@pytest.mark.trio
async def test_invalid_signature(testbed, alice2):
    exc_msg = "Cannot decrypt vlob: Signature was forged or corrupt"
    await testbed.run(author_signkey=alice2.signing_key, exc_msg=exc_msg)


@pytest.mark.trio
async def test_invalid_author(testbed, alice2):
    # Invalid author field in manifest
    exc_msg = "Cannot decrypt vlob: Invalid author: expected `alice@dev1`, got `alice@dev2`"
    await testbed.run(signed_author=alice2.device_id, exc_msg=exc_msg)

    # Invalid expected author stored in backend
    exc_msg = "Cannot decrypt vlob: Signature was forged or corrupt"
    await testbed.run(backend_author=alice2.device_id, exc_msg=exc_msg)


@pytest.mark.trio
async def test_invalid_timestamp(testbed, alice, alice2):
    bad_timestamp = Pendulum(2000, 1, 3)

    # Invalid timestamp field in manifest
    exc_msg = "Cannot decrypt vlob: Invalid timestamp: expected `2000-01-02T00:00:00+00:00`, got `2000-01-03T00:00:00+00:00`"
    await testbed.run(signed_timestamp=bad_timestamp, exc_msg=exc_msg)

    # Invalid expected timestamp stored in backend
    exc_msg = "Cannot decrypt vlob: Invalid timestamp: expected `2000-01-03T00:00:00+00:00`, got `2000-01-02T00:00:00+00:00`"
    await testbed.run(backend_timestamp=bad_timestamp, exc_msg=exc_msg)


@pytest.mark.trio
async def test_no_user_certif(testbed, alice, bob):
    # Data created before workspace manifest access
    exc_msg = "Manifest was created at 2000-01-02T00:00:00+00:00 by `bob@dev1` which had no right to access the workspace at that time"
    await testbed.run(
        backend_author=bob.device_id,
        signed_author=bob.device_id,
        author_signkey=bob.signing_key,
        exc_msg=exc_msg,
    )
