# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from parsec.datetime import DateTime

from parsec.core.fs import FSError


@pytest.fixture
def testbed(running_backend, alice_user_fs, alice):
    class TestBed:
        def __init__(self):
            self._next_version = 2
            self.defaults = {
                "local_manifest": alice_user_fs.get_user_manifest(),
                "blob": None,
                "signed_author": alice.device_id,
                "backend_author": alice.device_id,
                "signed_timestamp": DateTime(2000, 1, 2),
                "backend_timestamp": DateTime(2000, 1, 2),
                "author_signkey": alice.signing_key,
                "user_manifest_key": alice.user_manifest_key,
            }

        async def run(self, **kwargs):
            options = {**self.defaults, **kwargs}

            if options["blob"] is None:
                to_sync_um = options["local_manifest"].to_remote(
                    author=options["signed_author"], timestamp=options["signed_timestamp"]
                )
                options["blob"] = to_sync_um.dump_sign_and_encrypt(
                    author_signkey=options["author_signkey"], key=options["user_manifest_key"]
                )

            await running_backend.backend.vlob.update(
                organization_id=alice.organization_id,
                author=options["backend_author"],
                encryption_revision=1,
                vlob_id=alice.user_manifest_id,
                version=self._next_version,
                timestamp=options["backend_timestamp"],
                blob=options["blob"],
            )
            self._next_version += 1
            # This should trigger FSError
            await alice_user_fs.sync()

    return TestBed()


@pytest.mark.trio
async def test_empty_blob(testbed):
    with pytest.raises(FSError) as exc:
        await testbed.run(blob=b"")
    assert "Invalid user manifest" in str(exc.value)


@pytest.mark.trio
async def test_invalid_signature(testbed, alice2):
    with pytest.raises(FSError) as exc:
        await testbed.run(author_signkey=alice2.signing_key)
    assert str(exc.value) == "Invalid user manifest: Signature was forged or corrupt"


@pytest.mark.trio
async def test_invalid_author(testbed, alice2):
    # Invalid author field in manifest
    with pytest.raises(FSError) as exc:
        await testbed.run(signed_author=alice2.device_id)
    assert (
        str(exc.value)
        == "Invalid user manifest: Invalid author: expected `alice@dev1`, got `alice@dev2`"
    )

    # Invalid expected author stored in backend
    with pytest.raises(FSError) as exc:
        await testbed.run(backend_author=alice2.device_id)
    assert str(exc.value) == "Invalid user manifest: Signature was forged or corrupt"


@pytest.mark.trio
async def test_invalid_timestamp(testbed, alice, alice2):
    bad_timestamp = DateTime(2000, 1, 3)
    # Invalid timestamp field in manifest
    with pytest.raises(FSError) as exc:
        await testbed.run(signed_timestamp=bad_timestamp)
    assert (
        str(exc.value)
        == "Invalid user manifest: Invalid timestamp: expected `2000-01-02T00:00:00Z`, got `2000-01-03T00:00:00Z`"
    )

    # Invalid expected timestamp stored in backend
    with pytest.raises(FSError) as exc:
        await testbed.run(backend_timestamp=bad_timestamp)
    assert (
        str(exc.value)
        == "Invalid user manifest: Invalid timestamp: expected `2000-01-03T00:00:00Z`, got `2000-01-02T00:00:00Z`"
    )


@pytest.mark.trio
async def test_create_workspace_bad_name(alice_user_fs):
    with pytest.raises(ValueError):
        await alice_user_fs.workspace_create("..")


@pytest.mark.trio
async def test_rename_workspace_bad_name(alice_user_fs):
    wid = await alice_user_fs.workspace_create("w")
    with pytest.raises(ValueError):
        await alice_user_fs.workspace_rename(wid, "..")
