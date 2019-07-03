# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from hashlib import sha256
import pendulum
from pendulum import Pendulum
from typing import Dict, Optional

from parsec.serde import SerdeError
from parsec.crypto import (
    build_realm_self_role_certificate,
    encrypt_signed_msg_with_secret_key,
    decrypt_raw_with_secret_key,
    encrypt_raw_with_secret_key,
    decrypt_and_verify_signed_msg_with_secret_key,
    unsecure_read_realm_role_certificate,
    verify_realm_role_certificate,
    CryptoError,
)
from parsec.types import UserID
from parsec.api.protocole import RealmRole
from parsec.core.backend_connection import (
    BackendCmdsBadResponse,
    BackendCmdsInMaintenance,
    BackendCmdsAlreadyExists,
    BackendCmdsNotFound,
    BackendCmdsNotAllowed,
    BackendCmdsBadVersion,
    BackendNotAvailable,
    BackendConnectionError,
)
from parsec.core.types import EntryID, BlockAccess, remote_manifest_serializer, Manifest
from parsec.core.fs.exceptions import (
    FSError,
    FSRemoteSyncError,
    FSRemoteManifestNotFound,
    FSRemoteManifestNotFoundBadVersion,
    FSRemoteBlockNotFound,
    FSBackendOfflineError,
    FSWorkspaceInMaintenance,
    FSBadEncryptionRevision,
    FSWorkspaceNoAccess,
)


class RemoteLoader:
    def __init__(
        self,
        device,
        workspace_id,
        get_workspace_entry,
        backend_cmds,
        remote_device_manager,
        local_storage,
    ):
        self.device = device
        self.workspace_id = workspace_id
        self.get_workspace_entry = get_workspace_entry
        self.backend_cmds = backend_cmds
        self.remote_device_manager = remote_device_manager
        self.local_storage = local_storage

    async def load_realm_roles(
        self, realm_id: Optional[EntryID] = None, since: Optional[Pendulum] = None
    ) -> Dict[UserID, RealmRole]:
        """
        Raises:
            FSError
            FSBackendOfflineError
            FSWorkspaceNoAccess
        """
        try:
            unverifieds = await self.backend_cmds.realm_get_role_certificates(
                realm_id or self.workspace_id
            )

        except BackendNotAvailable as exc:
            raise FSBackendOfflineError(str(exc)) from exc

        except BackendCmdsNotAllowed as exc:
            # Seems we lost the access to the realm
            raise FSWorkspaceNoAccess(f"Not allowed to access this realm") from exc

        except BackendConnectionError as exc:
            raise FSError(f"Cannot retrieve realm roles: {exc}") from exc

        try:
            # Must read unverified certificates to access metadata
            unsecure_certifs = sorted(
                [
                    (
                        unsecure_read_realm_role_certificate(unverified.realm_role_certificate),
                        unverified.realm_role_certificate,
                    )
                    for unverified in unverifieds
                ],
                key=lambda x: x[0].certified_on,
            )

            current_roles = {}
            owner_only = (RealmRole.OWNER,)
            owner_or_manager = (RealmRole.OWNER, RealmRole.MANAGER)

            # Now verify each certif
            for unsecure_certif, raw_certif in unsecure_certifs:
                author = await self.remote_device_manager.get_device(unsecure_certif.certified_by)

                verify_realm_role_certificate(raw_certif, author.device_id, author.verify_key)

                # Make sure author had the right to do this
                existing_user_role = current_roles.get(unsecure_certif.user_id)
                if not current_roles and unsecure_certif.user_id == author.user_id:
                    # First user is autosigned
                    needed_roles = (None,)
                elif (
                    existing_user_role in owner_or_manager
                    or unsecure_certif.role in owner_or_manager
                ):
                    needed_roles = owner_only
                else:
                    needed_roles = owner_or_manager
                if current_roles.get(unsecure_certif.certified_by.user_id) not in needed_roles:
                    raise FSError(
                        f"Invalid realm role certificates: "
                        f"{unsecure_certif.certified_by} has not right to give "
                        f"{unsecure_certif.role} role to {unsecure_certif.user_id} "
                        f"on {unsecure_certif.certified_on}"
                    )

                current_roles[unsecure_certif.user_id] = unsecure_certif.role

        # Decryption error
        except CryptoError as exc:
            raise FSError(f"Invalid realm role certificates: {exc}") from exc

        return current_roles

    async def load_block(self, access: BlockAccess) -> bytes:
        """
        Raises:
            FSError
            FSRemoteBlockNotFound
            FSBackendOfflineError
            FSWorkspaceInMaintenance
        """
        # Download
        try:
            ciphered_block = await self.backend_cmds.block_read(access.id)

        # Block not found
        except BackendCmdsNotFound as exc:
            raise FSRemoteBlockNotFound(access) from exc

        # Backend not available
        except BackendNotAvailable as exc:
            raise FSBackendOfflineError(str(exc)) from exc

        # Workspace in maintenance
        except BackendCmdsInMaintenance as exc:
            raise FSWorkspaceInMaintenance(
                f"Cannot download block while the workspace in maintenance"
            ) from exc

        # Another backend error
        except BackendConnectionError as exc:
            raise FSError(f"Cannot download block: {exc}") from exc

        # Decryption
        try:
            block = decrypt_raw_with_secret_key(access.key, ciphered_block)

        # Decryption error
        except CryptoError as exc:
            raise FSError(f"Cannot decrypt block: {exc}") from exc

        # TODO: let encryption manager do the digest check ?
        # TODO: is digest even useful ? Given nacl.secret.Box does digest check
        # on the ciphered data they cannot be tempered. And given each block
        # has an unique key, valid blocks cannot be switched together.
        assert sha256(block).hexdigest() == access.digest, access
        self.local_storage.set_clean_block(access.id, block)
        return block

    async def upload_block(self, access: BlockAccess, data: bytes):
        """
        Raises:
            FSError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
        """
        # Encryption
        try:
            ciphered = encrypt_raw_with_secret_key(access.key, data)

        # Encryption error
        except CryptoError as exc:
            raise FSError(f"Cannot encrypt block: {exc}") from exc

        # Upload block
        try:
            await self.backend_cmds.block_create(access.id, self.workspace_id, ciphered)

        # Block already exists
        except BackendCmdsAlreadyExists:
            # Ignore exception if the block has already been uploaded
            # This might happen when a failure occurs before the local storage is updated
            pass

        # Backend is not available
        except BackendNotAvailable as exc:
            raise FSBackendOfflineError(str(exc)) from exc

        # Workspace is in maintenance
        except BackendCmdsInMaintenance as exc:
            raise FSWorkspaceInMaintenance(
                f"Cannot upload block while the workspace in maintenance"
            ) from exc

        # Another backend error
        except BackendConnectionError as exc:
            raise FSError(f"Cannot upload block: {exc}") from exc

        # Update local storage
        self.local_storage.clear_block(access.id)
        self.local_storage.set_clean_block(access.id, data)

    async def load_manifest(
        self, entry_id: EntryID, version: int = None, timestamp: Pendulum = None
    ) -> Manifest:
        """
        Raises:
            FSError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
            FSRemoteManifestNotFound
            FSBadEncryptionRevision
        """
        # Download the vlob
        workspace_entry = self.get_workspace_entry()
        try:
            args = await self.backend_cmds.vlob_read(
                workspace_entry.encryption_revision, entry_id, version=version, timestamp=timestamp
            )

        # Vlob is not found
        except BackendCmdsNotFound as exc:
            raise FSRemoteManifestNotFound(entry_id) from exc

        # Backend is not available
        except BackendNotAvailable as exc:
            raise FSBackendOfflineError(str(exc)) from exc

        # Workspace is in maintenance
        except BackendCmdsInMaintenance as exc:
            raise FSWorkspaceInMaintenance(
                f"Cannot download vlob while the workspace is in maintenance"
            ) from exc

        except BackendCmdsBadResponse as exc:
            if exc.status == "bad_version":
                raise FSRemoteManifestNotFoundBadVersion(entry_id)
            if exc.status == "not_found":
                raise FSRemoteManifestNotFound(entry_id)
            elif exc.status == "bad_encryption_revision":
                raise FSBadEncryptionRevision(
                    f"Cannot fetch vlob {entry_id}: Bad encryption revision provided"
                ) from exc
            else:
                raise FSError(f"Cannot fetch vlob {entry_id}: {exc}") from exc

        # Another backend error
        except BackendConnectionError as exc:
            raise FSError(f"Cannot fetch vlob {entry_id}: {exc}") from exc

        expected_author_id, expected_timestamp, expected_version, blob = args
        if version not in (None, expected_version):
            raise FSError(
                f"Backend returned invalid version for vlob {entry_id} (expecting {version}, got {expected_version})"
            )

        author = await self.remote_device_manager.get_device(expected_author_id)

        # Vlob decryption
        try:
            raw = decrypt_and_verify_signed_msg_with_secret_key(
                workspace_entry.key, blob, expected_author_id, author.verify_key, expected_timestamp
            )

        # Decryption error
        except CryptoError as exc:
            raise FSError(f"Cannot decrypt vlob: {exc}") from exc

        # Vlob deserialization
        try:
            remote_manifest = remote_manifest_serializer.loads(raw)

        # Deserialization error
        except SerdeError as exc:
            raise FSError(f"Cannot deserialize vlob: {exc}") from exc

        if remote_manifest.version != expected_version:
            raise FSError(
                f"Vlob {entry_id} version mismatch between signed metadata ({remote_manifest.version}) and backend ({expected_version})"
            )
        if remote_manifest.author != expected_author_id:
            raise FSError(
                f"Vlob {entry_id} author mismatch between signed metadata ({remote_manifest.author}) and backend ({expected_author_id})"
            )

        # TODO: also store access id in remote_manifest and check it here
        return remote_manifest

    async def create_realm(self, realm_id: EntryID):
        """
        Raises:
            FSError
            FSBackendOfflineError
        """
        certif = build_realm_self_role_certificate(
            self.device.device_id, self.device.signing_key, realm_id, pendulum.now()
        )

        try:
            await self.backend_cmds.realm_create(certif)

        except BackendCmdsBadResponse as exc:
            if exc.status == "already_exists":
                # It's possible a previous attempt to create this realm
                # succeeded but we didn't receive the confirmation, hence
                # we play idempotent here.
                return
            else:
                raise FSError(f"Cannot create realm {realm_id}: {exc}") from exc

        except BackendNotAvailable as exc:
            raise FSBackendOfflineError(str(exc)) from exc

        except BackendConnectionError as exc:
            raise FSError(f"Cannot create realm {realm_id}: {exc}") from exc

    async def upload_manifest(self, entry_id: EntryID, manifest: Manifest):
        """
        Raises:
            FSError
            FSRemoteSyncError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
            FSBadEncryptionRevision
        """
        now = pendulum.now()
        assert manifest.author == self.device.device_id
        workspace_entry = self.get_workspace_entry()

        # Manifest serialization
        try:
            raw = remote_manifest_serializer.dumps(manifest)

        # Serialization error
        except SerdeError as exc:
            raise FSError(f"Cannot serialize vlob: {exc}") from exc

        # Vlob encryption
        try:
            ciphered = encrypt_signed_msg_with_secret_key(
                self.device.device_id, self.device.signing_key, workspace_entry.key, raw, now
            )

        # Encryption error
        except CryptoError as exc:
            raise FSError(f"Cannot encrypt vlob: {exc}") from exc

        # Upload the vlob
        if manifest.version == 1:
            await self._vlob_create(workspace_entry.encryption_revision, entry_id, ciphered, now)
        else:
            await self._vlob_update(
                workspace_entry.encryption_revision, entry_id, ciphered, now, manifest.version
            )

    async def _vlob_create(
        self, encryption_revision: int, entry_id: EntryID, ciphered: bytes, now: Pendulum
    ):
        """
        Raises:
            FSError
            FSRemoteSyncError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
            FSBadEncryptionRevision
        """

        # Vlob updload
        try:
            await self.backend_cmds.vlob_create(
                self.workspace_id, encryption_revision, entry_id, now, ciphered
            )

        # Vlob alread exists
        except BackendCmdsAlreadyExists as exc:
            raise FSRemoteSyncError(entry_id) from exc

        # Backend not available
        except BackendNotAvailable as exc:
            raise FSBackendOfflineError(str(exc)) from exc

        # Workspace in maintenance
        except BackendCmdsInMaintenance as exc:
            raise FSWorkspaceInMaintenance(
                f"Cannot create vlob while the workspace is in maintenance"
            ) from exc

        except BackendCmdsBadResponse as exc:
            if exc.status == "already_exists":
                raise FSRemoteSyncError(entry_id)
            elif exc.status == "bad_encryption_revision":
                raise FSBadEncryptionRevision(
                    f"Cannot create vlob {entry_id}: Bad encryption revision provided"
                ) from exc
            else:
                raise FSError(f"Cannot create vlob {entry_id}: {exc}") from exc

        # Another backend error
        except BackendConnectionError as exc:
            raise FSError(f"Cannot update vlob: {exc}") from exc

    async def _vlob_update(
        self,
        encryption_revision: int,
        entry_id: EntryID,
        ciphered: bytes,
        now: Pendulum,
        version: int,
    ):
        """
        Raises:
            FSError
            FSRemoteSyncError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
        """
        # Vlob upload
        try:
            await self.backend_cmds.vlob_update(
                encryption_revision, entry_id, version, now, ciphered
            )

        # Vlob not found
        except BackendCmdsNotFound as exc:
            raise FSRemoteSyncError(entry_id) from exc

        # Workspace in maintenance
        except BackendCmdsInMaintenance as exc:
            raise FSWorkspaceInMaintenance(
                f"Cannot create vlob while the workspace is in maintenance"
            ) from exc

        # Versions do not match
        except BackendCmdsBadVersion as exc:
            raise FSRemoteSyncError(entry_id) from exc

        except BackendCmdsBadResponse as exc:
            if exc.status == "bad_version":
                raise FSRemoteSyncError(entry_id)
            elif exc.status == "bad_encryption_revision":
                raise FSBadEncryptionRevision(
                    f"Cannot update vlob {entry_id}: Bad encryption revision provided"
                ) from exc
            else:
                raise FSError(f"Cannot update vlob {entry_id}: {exc}") from exc

        # Backend not available
        except BackendNotAvailable as exc:
            raise FSBackendOfflineError(str(exc)) from exc

        # Another backend error
        except BackendConnectionError as exc:
            raise FSError(f"Cannot update vlob: {exc}") from exc

    def to_timestamped(self, timestamp):
        return RemoteLoaderTimestamped(self, timestamp)


class RemoteLoaderTimestamped(RemoteLoader):
    def __init__(self, remote_loader: RemoteLoader, timestamp: Pendulum):
        self.device = remote_loader.device
        self.workspace_id = remote_loader.workspace_id
        self.get_workspace_entry = remote_loader.get_workspace_entry
        self.backend_cmds = remote_loader.backend_cmds
        self.remote_device_manager = remote_loader.remote_device_manager
        self.local_storage = remote_loader.local_storage.to_timestamped(timestamp)
        self.timestamp = timestamp

    async def upload_block(self, *e, **ke):
        raise FSError(f"Cannot upload block through a timestamped remote loader")

    async def load_manifest(
        self, entry_id: EntryID, version: int = None, timestamp: Pendulum = None
    ) -> Manifest:
        if timestamp is not None and timestamp != self.timestamp:
            raise FSError(
                f"Cannot load a manifest at a different timestamp through a timestamped remote loader"
            )
        return await super().load_manifest(entry_id, version=version, timestamp=self.timestamp)

    async def upload_manifest(self, *e, **ke):
        raise FSError(f"Cannot upload manifest through a timestamped remote loader")

    async def _vlob_create(self, *e, **ke):
        raise FSError(f"Cannot create vlob through a timestamped remote loader")

    async def _vlob_update(self, *e, **ke):
        raise FSError(f"Cannot update vlob through a timestamped remote loader")
