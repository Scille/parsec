# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from contextlib import contextmanager
from typing import Dict, Optional, List, Tuple, cast, Iterator, Callable, Awaitable

from pendulum import DateTime, now as pendulum_now

from parsec.utils import timestamps_in_the_ballpark
from parsec.crypto import HashDigest, CryptoError
from parsec.api.protocol import UserID, DeviceID, RealmRole
from parsec.api.data import (
    DataError,
    BlockAccess,
    RealmRoleCertificateContent,
    BaseManifest as BaseRemoteManifest,
)

from parsec.core.types import EntryID, ChunkID, LocalDevice, WorkspaceEntry

from parsec.core.backend_connection import (
    BackendConnectionError,
    BackendNotAvailable,
    BackendAuthenticatedCmds,
)
from parsec.api.data import (
    UserCertificateContent,
    DeviceCertificateContent,
    RevokedUserCertificateContent,
)
from parsec.core.remote_devices_manager import (
    RemoteDevicesManager,
    RemoteDevicesManagerBackendOfflineError,
    RemoteDevicesManagerError,
    RemoteDevicesManagerUserNotFoundError,
    RemoteDevicesManagerDeviceNotFoundError,
    RemoteDevicesManagerInvalidTrustchainError,
)
from parsec.core.fs.exceptions import (
    FSError,
    FSRemoteSyncError,
    FSRemoteOperationError,
    FSRemoteManifestNotFound,
    FSRemoteManifestNotFoundBadVersion,
    FSRemoteManifestNotFoundBadTimestamp,
    FSRemoteBlockNotFound,
    FSBackendOfflineError,
    FSWorkspaceInMaintenance,
    FSBadEncryptionRevision,
    FSWorkspaceNoReadAccess,
    FSWorkspaceNoWriteAccess,
    FSUserNotFoundError,
    FSDeviceNotFoundError,
    FSInvalidTrustchainEror,
)
from parsec.core.fs.storage import BaseWorkspaceStorage


@contextmanager
def translate_remote_devices_manager_errors() -> Iterator[None]:
    try:
        yield
    except RemoteDevicesManagerBackendOfflineError as exc:
        raise FSBackendOfflineError(str(exc)) from exc
    except RemoteDevicesManagerUserNotFoundError as exc:
        raise FSUserNotFoundError(str(exc)) from exc
    except RemoteDevicesManagerDeviceNotFoundError as exc:
        raise FSDeviceNotFoundError(str(exc)) from exc
    except RemoteDevicesManagerInvalidTrustchainError as exc:
        raise FSInvalidTrustchainEror(str(exc)) from exc
    except RemoteDevicesManagerError as exc:
        raise FSRemoteOperationError(str(exc)) from exc


@contextmanager
def translate_backend_cmds_errors() -> Iterator[None]:
    try:
        yield
    except BackendNotAvailable as exc:
        raise FSBackendOfflineError(str(exc)) from exc
    except BackendConnectionError as exc:
        raise FSRemoteOperationError(str(exc)) from exc


class UserRemoteLoader:
    def __init__(
        self,
        device: LocalDevice,
        workspace_id: EntryID,
        get_workspace_entry: Callable[[], WorkspaceEntry],
        get_previous_workspace_entry: Callable[[], Awaitable[WorkspaceEntry]],
        backend_cmds: BackendAuthenticatedCmds,
        remote_devices_manager: RemoteDevicesManager,
    ):
        self.device = device
        self.workspace_id = workspace_id
        self.get_workspace_entry = get_workspace_entry
        self.get_previous_workspace_entry = get_previous_workspace_entry
        self.backend_cmds = backend_cmds
        self.remote_devices_manager = remote_devices_manager
        self._realm_role_certificates_cache: Optional[List[RealmRoleCertificateContent]] = None
        self._realm_role_certificates_cache_timestamp: Optional[DateTime] = None

    async def _get_user_realm_role_at(
        self, user_id: UserID, timestamp: DateTime
    ) -> Optional[RealmRole]:
        if (
            self._realm_role_certificates_cache is None
            or self._realm_role_certificates_cache_timestamp is None
            or self._realm_role_certificates_cache_timestamp <= timestamp
        ):
            cache_timestamp = pendulum_now()
            self._realm_role_certificates_cache, _ = await self._load_realm_role_certificates()
            # Set the cache timestamp in two times to avoid invalid value in case of exception
            self._realm_role_certificates_cache_timestamp = cache_timestamp

        assert self._realm_role_certificates_cache is not None
        for certif in reversed(self._realm_role_certificates_cache):
            if certif.user_id == user_id and certif.timestamp <= timestamp:
                return certif.role
        else:
            return None

    async def _load_realm_role_certificates(
        self, realm_id: Optional[EntryID] = None
    ) -> Tuple[List[RealmRoleCertificateContent], Dict[UserID, RealmRole]]:
        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.realm_get_role_certificates(realm_id or self.workspace_id)
        if rep["status"] == "not_allowed":
            # Seems we lost the access to the realm
            raise FSWorkspaceNoReadAccess("Cannot get workspace roles: no read access")
        elif rep["status"] != "ok":
            raise FSError(f"Cannot retrieve workspace roles: `{rep['status']}`")

        try:
            # Must read unverified certificates to access metadata
            unsecure_certifs = sorted(
                [
                    (RealmRoleCertificateContent.unsecure_load(uv_role), uv_role)
                    for uv_role in rep["certificates"]
                ],
                key=lambda x: x[0].timestamp,
            )

            current_roles: Dict[UserID, RealmRole] = {}
            owner_only = (RealmRole.OWNER,)
            owner_or_manager = (RealmRole.OWNER, RealmRole.MANAGER)

            # Now verify each certif
            for unsecure_certif, raw_certif in unsecure_certifs:

                with translate_remote_devices_manager_errors():
                    author = await self.remote_devices_manager.get_device(unsecure_certif.author)

                RealmRoleCertificateContent.verify_and_load(
                    raw_certif,
                    author_verify_key=author.verify_key,
                    expected_author=author.device_id,
                )

                # Make sure author had the right to do this
                existing_user_role = current_roles.get(unsecure_certif.user_id)
                if not current_roles and unsecure_certif.user_id == author.device_id.user_id:
                    # First user is autosigned
                    needed_roles: Tuple[Optional[RealmRole], ...] = (None,)
                elif (
                    existing_user_role in owner_or_manager
                    or unsecure_certif.role in owner_or_manager
                ):
                    needed_roles = owner_only
                else:
                    needed_roles = owner_or_manager
                # TODO: typing, author is optional in base.py but it seems that manifests always have an author (no RVK)
                if (
                    current_roles.get(cast(DeviceID, unsecure_certif.author).user_id)
                    not in needed_roles
                ):
                    raise FSError(
                        f"Invalid realm role certificates: "
                        f"{unsecure_certif.author} has not right to give "
                        f"{unsecure_certif.role} role to {unsecure_certif.user_id} "
                        f"on {unsecure_certif.timestamp}"
                    )

                if unsecure_certif.role is None:
                    current_roles.pop(unsecure_certif.user_id, None)
                else:
                    current_roles[unsecure_certif.user_id] = unsecure_certif.role

        # Decryption error
        except DataError as exc:
            raise FSError(f"Invalid realm role certificates: {exc}") from exc

        # Now unsecure_certifs is no longer unsecure given we have valided it items
        return [c for c, _ in unsecure_certifs], current_roles

    async def load_realm_role_certificates(
        self, realm_id: Optional[EntryID] = None
    ) -> List[RealmRoleCertificateContent]:
        """
        Raises:
            FSError
            FSBackendOfflineError
            FSRemoteOperationError
            FSWorkspaceNoAccess
            FSUserNotFoundError
            FSDeviceNotFoundError
            FSInvalidTrustchainError
        """
        certificates, _ = await self._load_realm_role_certificates(realm_id)
        return certificates

    async def load_realm_current_roles(
        self, realm_id: Optional[EntryID] = None
    ) -> Dict[UserID, RealmRole]:
        """
        Raises:
            FSError
            FSBackendOfflineError
            FSRemoteOperationError
            FSWorkspaceNoAccess
            FSUserNotFoundError
            FSDeviceNotFoundError
            FSInvalidTrustchainError
        """
        _, current_roles = await self._load_realm_role_certificates(realm_id)
        return current_roles

    async def get_user(
        self, user_id: UserID, no_cache: bool = False
    ) -> Tuple[UserCertificateContent, Optional[RevokedUserCertificateContent]]:
        """
        Raises:
            FSRemoteOperationError
            FSBackendOfflineError
            FSUserNotFoundError
            FSInvalidTrustchainError
        """
        with translate_remote_devices_manager_errors():
            return await self.remote_devices_manager.get_user(user_id, no_cache=no_cache)

    async def get_device(
        self, device_id: DeviceID, no_cache: bool = False
    ) -> DeviceCertificateContent:
        """
        Raises:
            FSRemoteOperationError
            FSBackendOfflineError
            FSUserNotFoundError
            FSDeviceNotFoundError
            FSInvalidTrustchainError
        """
        with translate_remote_devices_manager_errors():
            return await self.remote_devices_manager.get_device(device_id, no_cache=no_cache)

    async def list_versions(self, entry_id: EntryID) -> Dict[int, Tuple[DateTime, DeviceID]]:
        """
        Raises:
            FSError
            FSRemoteOperationError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
            FSRemoteManifestNotFound
        """
        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.vlob_list_versions(entry_id)
        if rep["status"] == "not_allowed":
            # Seems we lost the access to the realm
            raise FSWorkspaceNoReadAccess("Cannot load manifest: no read access")
        elif rep["status"] == "not_found":
            raise FSRemoteManifestNotFound(entry_id)
        elif rep["status"] == "in_maintenance":
            raise FSWorkspaceInMaintenance(
                "Cannot download vlob while the workspace is in maintenance"
            )
        elif rep["status"] != "ok":
            raise FSError(f"Cannot fetch vlob {entry_id}: `{rep['status']}`")

        return rep["versions"]

    async def create_realm(self, realm_id: EntryID) -> None:
        """
        Raises:
            FSError
            FSRemoteOperationError
            FSBackendOfflineError
        """
        certif = RealmRoleCertificateContent.build_realm_root_certif(
            author=self.device.device_id, timestamp=pendulum_now(), realm_id=realm_id
        ).dump_and_sign(self.device.signing_key)

        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.realm_create(certif)

        if rep["status"] == "already_exists":
            # It's possible a previous attempt to create this realm
            # succeeded but we didn't receive the confirmation, hence
            # we play idempotent here.
            return
        elif rep["status"] != "ok":
            raise FSError(f"Cannot create realm {realm_id}: `{rep['status']}`")


class RemoteLoader(UserRemoteLoader):
    def __init__(
        self,
        device: LocalDevice,
        workspace_id: EntryID,
        get_workspace_entry: Callable[[], WorkspaceEntry],
        get_previous_workspace_entry: Callable[[], Awaitable[WorkspaceEntry]],
        backend_cmds: BackendAuthenticatedCmds,
        remote_devices_manager: RemoteDevicesManager,
        local_storage: BaseWorkspaceStorage,
    ):
        super().__init__(
            device,
            workspace_id,
            get_workspace_entry,
            get_previous_workspace_entry,
            backend_cmds,
            remote_devices_manager,
        )
        self.local_storage = local_storage

    async def load_blocks(self, accesses: List[BlockAccess]) -> None:
        """
        Raises:
            FSError
            FSRemoteBlockNotFound
            FSBackendOfflineError
            FSWorkspaceInMaintenance
        """
        for access in accesses:
            await self.load_block(access)

    async def load_block(self, access: BlockAccess) -> None:
        """
        Raises:
            FSError
            FSRemoteBlockNotFound
            FSBackendOfflineError
            FSRemoteOperationError
            FSWorkspaceInMaintenance
            FSWorkspaceNoAccess
        """
        # Download
        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.block_read(access.id)
        if rep["status"] == "not_found":
            raise FSRemoteBlockNotFound(access)
        elif rep["status"] == "not_allowed":
            # Seems we lost the access to the realm
            raise FSWorkspaceNoReadAccess("Cannot load block: no read access")
        elif rep["status"] == "in_maintenance":
            raise FSWorkspaceInMaintenance(
                "Cannot download block while the workspace in maintenance"
            )
        elif rep["status"] != "ok":
            raise FSError(f"Cannot download block: `{rep['status']}`")

        # Decryption
        try:
            block = access.key.decrypt(rep["block"])

        # Decryption error
        except CryptoError as exc:
            raise FSError(f"Cannot decrypt block: {exc}") from exc

        # TODO: let encryption manager do the digest check ?
        assert HashDigest.from_data(block) == access.digest, access
        await self.local_storage.set_clean_block(access.id, block)

    async def upload_block(self, access: BlockAccess, data: bytes) -> None:
        """
        Raises:
            FSError
            FSBackendOfflineError
            FSRemoteOperationError
            FSWorkspaceInMaintenance
            FSWorkspaceNoAccess
        """
        # Encryption
        try:
            ciphered = access.key.encrypt(data)

        # Encryption error
        except CryptoError as exc:
            raise FSError(f"Cannot encrypt block: {exc}") from exc

        # Upload block
        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.block_create(access.id, self.workspace_id, ciphered)

        if rep["status"] == "already_exists":
            # Ignore exception if the block has already been uploaded
            # This might happen when a failure occurs before the local storage is updated
            pass
        elif rep["status"] == "not_allowed":
            # Seems we lost the access to the realm
            raise FSWorkspaceNoWriteAccess("Cannot upload block: no write access")
        elif rep["status"] == "in_maintenance":
            raise FSWorkspaceInMaintenance("Cannot upload block while the workspace in maintenance")
        elif rep["status"] != "ok":
            raise FSError(f"Cannot upload block: {rep}")

        # Update local storage
        await self.local_storage.set_clean_block(access.id, data)
        await self.local_storage.clear_chunk(ChunkID(access.id), miss_ok=True)

    async def load_manifest(
        self,
        entry_id: EntryID,
        version: Optional[int] = None,
        timestamp: Optional[DateTime] = None,
        expected_backend_timestamp: Optional[DateTime] = None,
        workspace_entry: Optional[WorkspaceEntry] = None,
    ) -> BaseRemoteManifest:
        """
        Download a manifest.

        Only one from version or timestamp parameters can be specified at the same time.
        expected_backend_timestamp enables to check a timestamp against the one returned by the
        backend.

        Raises:
            FSError
            FSBackendOfflineError
            FSRemoteOperationError
            FSWorkspaceInMaintenance
            FSRemoteManifestNotFound
            FSBadEncryptionRevision
            FSWorkspaceNoAccess
            FSUserNotFoundError
            FSDeviceNotFoundError
            FSInvalidTrustchainError
        """
        assert (
            timestamp is None or version is None
        ), "Either timestamp or version argument should be provided"
        # Get the current and requested workspace entry
        # They're usually the same, except when loading from a workspace while it's in maintenance
        current_workspace_entry = self.get_workspace_entry()
        workspace_entry = current_workspace_entry if workspace_entry is None else workspace_entry
        # Download the vlob
        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.vlob_read(
                workspace_entry.encryption_revision,
                entry_id,
                version=version,
                timestamp=timestamp if version is None else None,
            )
        # Special case for loading manifest while in maintenance.
        # This is done to allow users to fetch data from a workspace while it's being reencrypted.
        # If the workspace is in maintenance for another reason (such as garbage collection),
        # the recursive call to load manifest will simply also fail with an FSWorkspaceInMaintenance.
        if (
            rep["status"] == "in_maintenance"
            and workspace_entry.encryption_revision == current_workspace_entry.encryption_revision
        ):
            # Getting the last workspace entry with the previous encryption revision
            # requires one or several calls to the backend, meaning the following exceptions might get raised:
            # - FSError
            # - FSBackendOfflineError
            # - FSWorkspaceInMaintenance
            # It is fine to let those exceptions bubble up as there all valid reasons for failing to load a manifest.
            previous_workspace_entry = await self.get_previous_workspace_entry()
            # Make sure we don't fall into an infinite loop because of some other bug
            assert (
                previous_workspace_entry.encryption_revision
                < self.get_workspace_entry().encryption_revision
            )
            # Recursive call to `load_manifest`, requiring an older encryption revision than the current one
            return await self.load_manifest(
                entry_id,
                version=version,
                timestamp=timestamp,
                expected_backend_timestamp=expected_backend_timestamp,
                workspace_entry=previous_workspace_entry,
            )

        if rep["status"] == "not_found":
            raise FSRemoteManifestNotFound(entry_id)
        elif rep["status"] == "not_allowed":
            # Seems we lost the access to the realm
            raise FSWorkspaceNoReadAccess("Cannot load manifest: no read access")
        elif rep["status"] == "bad_version":
            raise FSRemoteManifestNotFoundBadVersion(entry_id)
        elif rep["status"] == "bad_timestamp":
            raise FSRemoteManifestNotFoundBadTimestamp(entry_id)
        elif rep["status"] == "bad_encryption_revision":
            raise FSBadEncryptionRevision(
                f"Cannot fetch vlob {entry_id}: Bad encryption revision provided"
            )
        elif rep["status"] == "in_maintenance":
            raise FSWorkspaceInMaintenance(
                "Cannot download vlob while the workspace is in maintenance"
            )
        elif rep["status"] != "ok":
            raise FSError(f"Cannot fetch vlob {entry_id}: `{rep['status']}`")

        expected_version = rep["version"]
        expected_author = rep["author"]
        expected_timestamp = rep["timestamp"]
        if version not in (None, expected_version):
            raise FSError(
                f"Backend returned invalid version for vlob {entry_id} (expecting {version}, "
                f"got {expected_version})"
            )

        if expected_backend_timestamp and expected_backend_timestamp != expected_timestamp:
            raise FSError(
                f"Backend returned invalid expected timestamp for vlob {entry_id} at version "
                f"{version} (expecting {expected_backend_timestamp}, got {expected_timestamp})"
            )

        with translate_remote_devices_manager_errors():
            author = await self.remote_devices_manager.get_device(expected_author)

        try:
            remote_manifest = BaseRemoteManifest.decrypt_verify_and_load(
                rep["blob"],
                key=workspace_entry.key,
                author_verify_key=author.verify_key,
                expected_author=expected_author,
                expected_timestamp=expected_timestamp,
                expected_version=expected_version,
                expected_id=entry_id,
            )
        except DataError as exc:
            raise FSError(f"Cannot decrypt vlob: {exc}") from exc

        # Finally make sure author was allowed to create this manifest
        role_at_timestamp = await self._get_user_realm_role_at(
            expected_author.user_id, expected_timestamp
        )
        if role_at_timestamp is None:
            raise FSError(
                f"Manifest was created at {expected_timestamp} by `{expected_author}` "
                "which had no right to access the workspace at that time"
            )
        elif role_at_timestamp == RealmRole.READER:
            raise FSError(
                f"Manifest was created at {expected_timestamp} by `{expected_author}` "
                "which had write right on the workspace at that time"
            )

        return remote_manifest

    async def upload_manifest(self, entry_id: EntryID, manifest: BaseRemoteManifest) -> None:
        """
        Raises:
            FSError
            FSRemoteSyncError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
            FSBadEncryptionRevision
        """
        assert manifest.author == self.device.device_id
        assert timestamps_in_the_ballpark(manifest.timestamp, pendulum_now())

        workspace_entry = self.get_workspace_entry()

        try:
            ciphered = manifest.dump_sign_and_encrypt(
                key=workspace_entry.key, author_signkey=self.device.signing_key
            )
        except DataError as exc:
            raise FSError(f"Cannot encrypt vlob: {exc}") from exc

        # Upload the vlob
        if manifest.version == 1:
            await self._vlob_create(
                workspace_entry.encryption_revision, entry_id, ciphered, manifest.timestamp
            )
        else:
            await self._vlob_update(
                workspace_entry.encryption_revision,
                entry_id,
                ciphered,
                manifest.timestamp,
                manifest.version,
            )

    async def _vlob_create(
        self, encryption_revision: int, entry_id: EntryID, ciphered: bytes, now: DateTime
    ) -> None:
        """
        Raises:
            FSError
            FSRemoteSyncError
            FSBackendOfflineError
            FSRemoteOperationError
            FSWorkspaceInMaintenance
            FSBadEncryptionRevision
            FSWorkspaceNoAccess
        """

        # Vlob upload
        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.vlob_create(
                self.workspace_id, encryption_revision, entry_id, now, ciphered
            )
        if rep["status"] == "already_exists":
            raise FSRemoteSyncError(entry_id)
        elif rep["status"] == "not_allowed":
            # Seems we lost the access to the realm
            raise FSWorkspaceNoWriteAccess("Cannot upload manifest: no write access")
        elif rep["status"] == "bad_encryption_revision":
            raise FSBadEncryptionRevision(
                f"Cannot create vlob {entry_id}: Bad encryption revision provided"
            )
        elif rep["status"] == "in_maintenance":
            raise FSWorkspaceInMaintenance(
                "Cannot create vlob while the workspace is in maintenance"
            )
        elif rep["status"] != "ok":
            raise FSError(f"Cannot create vlob {entry_id}: `{rep['status']}`")

    async def _vlob_update(
        self,
        encryption_revision: int,
        entry_id: EntryID,
        ciphered: bytes,
        now: DateTime,
        version: int,
    ) -> None:
        """
        Raises:
            FSError
            FSRemoteSyncError
            FSBackendOfflineError
            FSRemoteOperationError
            FSWorkspaceInMaintenance
            FSBadEncryptionRevision
            FSWorkspaceNoAccess
        """
        # Vlob upload
        with translate_backend_cmds_errors():
            rep = await self.backend_cmds.vlob_update(
                encryption_revision, entry_id, version, now, ciphered
            )

        if rep["status"] == "not_found":
            raise FSRemoteSyncError(entry_id)
        elif rep["status"] == "not_allowed":
            # Seems we lost the access to the realm
            raise FSWorkspaceNoWriteAccess("Cannot upload manifest: no write access")
        elif rep["status"] == "bad_version":
            raise FSRemoteSyncError(entry_id)
        elif rep["status"] == "bad_timestamp":
            # Quick and dirty fix before a better version with a retry loop : go offline so we
            # don't have to deal with another client updating manifest with a later timestamp
            raise FSBackendOfflineError(rep)
        elif rep["status"] == "bad_encryption_revision":
            raise FSBadEncryptionRevision(
                f"Cannot update vlob {entry_id}: Bad encryption revision provided"
            )
        elif rep["status"] == "in_maintenance":
            raise FSWorkspaceInMaintenance(
                "Cannot create vlob while the workspace is in maintenance"
            )
        elif rep["status"] != "ok":
            raise FSError(f"Cannot update vlob {entry_id}: `{rep['status']}`")

    def to_timestamped(self, timestamp: DateTime) -> "RemoteLoaderTimestamped":
        return RemoteLoaderTimestamped(self, timestamp)


class RemoteLoaderTimestamped(RemoteLoader):
    def __init__(self, remote_loader: RemoteLoader, timestamp: DateTime):
        self.device = remote_loader.device
        self.workspace_id = remote_loader.workspace_id
        self.get_workspace_entry = remote_loader.get_workspace_entry
        self.get_previous_workspace_entry = remote_loader.get_previous_workspace_entry
        self.backend_cmds = remote_loader.backend_cmds
        self.remote_devices_manager = remote_loader.remote_devices_manager
        self.local_storage = remote_loader.local_storage.to_timestamped(timestamp)
        self._realm_role_certificates_cache = None
        self._realm_role_certificates_cache_timestamp = None
        self.timestamp = timestamp

    async def upload_block(self, access: BlockAccess, data: bytes) -> None:
        raise FSError("Cannot upload block through a timestamped remote loader")

    async def load_manifest(
        self,
        entry_id: EntryID,
        version: Optional[int] = None,
        timestamp: Optional[DateTime] = None,
        expected_backend_timestamp: Optional[DateTime] = None,
        workspace_entry: Optional[WorkspaceEntry] = None,
    ) -> BaseRemoteManifest:
        """
        Allows to have manifests at all timestamps as it is needed by the versions method of either
        a WorkspaceFS or a WorkspaceFSTimestamped

        Only one from version or timestamp can be specified at the same time.
        expected_backend_timestamp enables to check a timestamp against the one returned by the
        backend.

        Raises:
            FSError
            FSBackendOfflineError
            FSWorkspaceInMaintenance
            FSRemoteManifestNotFound
            FSBadEncryptionRevision
            FSWorkspaceNoAccess
        """
        if timestamp is None and version is None:
            timestamp = self.timestamp
        return await super().load_manifest(
            entry_id,
            version=version,
            timestamp=timestamp,
            expected_backend_timestamp=expected_backend_timestamp,
            workspace_entry=workspace_entry,
        )

    async def upload_manifest(self, entry_id: EntryID, manifest: BaseRemoteManifest) -> None:
        raise FSError("Cannot upload manifest through a timestamped remote loader")

    async def _vlob_create(
        self, encryption_revision: int, entry_id: EntryID, ciphered: bytes, now: DateTime
    ) -> None:
        raise FSError("Cannot create vlob through a timestamped remote loader")

    async def _vlob_update(
        self,
        encryption_revision: int,
        entry_id: EntryID,
        ciphered: bytes,
        now: DateTime,
        version: int,
    ) -> None:
        raise FSError("Cannot update vlob through a timestamped remote loader")
