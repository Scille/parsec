# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Optional, Tuple

from parsec.api.data import (
    DeviceCertificateContent,
    RevokedUserCertificateContent,
    UserCertificateContent,
)
from parsec.api.protocol import DeviceID, UserID
from parsec.core.backend_connection import (
    APIV1_BackendAnonymousCmds,
    BackendAuthenticatedCmds,
    BackendConnectionError,
    BackendNotAvailable,
)
from parsec.core.trustchain import TrustchainContext, TrustchainError
from parsec.crypto import VerifyKey

DEFAULT_CACHE_VALIDITY = 60 * 60  # 1h


class RemoteDevicesManagerError(Exception):
    pass


class RemoteDevicesManagerBackendOfflineError(RemoteDevicesManagerError):
    pass


class RemoteDevicesManagerValidationError(RemoteDevicesManagerError):
    pass


class RemoteDevicesManagerPackingError(RemoteDevicesManagerError):
    pass


class RemoteDevicesManagerNotFoundError(RemoteDevicesManagerError):
    pass


class RemoteDevicesManagerInvalidTrustchainError(RemoteDevicesManagerError):
    pass


class RemoteDevicesManager:
    """
    Fetch users&devices from backend, verify their trustchain and keep
    a cache of them for a limited duration.
    """

    def __init__(
        self,
        backend_cmds: BackendAuthenticatedCmds,
        root_verify_key: VerifyKey,
        cache_validity: int = DEFAULT_CACHE_VALIDITY,
    ):
        self._backend_cmds = backend_cmds
        self._devices = {}
        self._users = {}
        self._trustchain_ctx = TrustchainContext(root_verify_key, cache_validity)

    @property
    def cache_validity(self) -> int:
        return self._trustchain_ctx.cache_validity

    def invalidate_user_cache(self, user_id: UserID) -> None:
        self._trustchain_ctx.invalidate_user_cache(user_id)

    async def get_user(
        self, user_id: UserID, no_cache: bool = False
    ) -> Tuple[UserCertificateContent, Optional[RevokedUserCertificateContent]]:
        """
        Raises:
            RemoteDevicesManagerError
            RemoteDevicesManagerBackendOfflineError
            RemoteDevicesManagerNotFoundError
            RemoteDevicesManagerInvalidTrustchainError
        """
        try:
            verified_user = None if no_cache else self._trustchain_ctx.get_user(user_id)
            verified_revoked_user = (
                None if no_cache else self._trustchain_ctx.get_revoked_user(user_id)
            )
        except TrustchainError as exc:
            raise RemoteDevicesManagerInvalidTrustchainError(exc) from exc
        if not verified_user:
            verified_user, verified_revoked_user, _ = await self.get_user_and_devices(
                user_id, no_cache=True
            )
        return verified_user, verified_revoked_user

    async def get_device(
        self, device_id: DeviceID, no_cache: bool = False
    ) -> DeviceCertificateContent:
        """
        Raises:
            RemoteDevicesManagerError
            RemoteDevicesManagerBackendOfflineError
            RemoteDevicesManagerNotFoundError
            RemoteDevicesManagerInvalidTrustchainError
        """
        try:
            verified_device = None if no_cache else self._trustchain_ctx.get_device(device_id)
        except TrustchainError as exc:
            raise RemoteDevicesManagerInvalidTrustchainError(exc) from exc
        if not verified_device:
            _, _, verified_devices = await self.get_user_and_devices(
                device_id.user_id, no_cache=True
            )
            try:
                verified_device = next(vd for vd in verified_devices if vd.device_id == device_id)

            except StopIteration:
                raise RemoteDevicesManagerNotFoundError(
                    f"User `{device_id.user_id}` doesn't have a device `{device_id}`"
                )
        return verified_device

    async def get_user_and_devices(
        self, user_id: UserID, no_cache: bool = False
    ) -> Tuple[
        UserCertificateContent,
        Optional[RevokedUserCertificateContent],
        List[DeviceCertificateContent],
    ]:
        """
        Note: unlike `get_user` and `get_device`, this method don't rely on cache
        considering only part of the devices to retrieve could be in cache.
        Raises:
            RemoteDevicesManagerError
            RemoteDevicesManagerBackendOfflineError
            RemoteDevicesManagerNotFoundError
            RemoteDevicesManagerInvalidTrustchainError
        """
        try:
            rep = await self._backend_cmds.user_get(user_id)
        except BackendNotAvailable as exc:
            raise RemoteDevicesManagerBackendOfflineError(
                f"User `{user_id}` is not in local cache and we are offline."
            ) from exc
        except BackendConnectionError as exc:
            raise RemoteDevicesManagerError(
                f"Failed to fetch user `{user_id}` from the backend: {exc}"
            ) from exc

        if rep["status"] == "not_found":
            raise RemoteDevicesManagerNotFoundError(f"User `{user_id}` doesn't exist in backend")
        elif rep["status"] != "ok":
            raise RemoteDevicesManagerError(f"Cannot fetch user {user_id}: `{rep['status']}`")

        try:
            return self._trustchain_ctx.load_user_and_devices(
                trustchain=rep["trustchain"],
                user_certif=rep["user_certificate"],
                revoked_user_certif=rep["revoked_user_certificate"],
                devices_certifs=rep["device_certificates"],
                expected_user_id=user_id,
            )
        except TrustchainError as exc:
            raise RemoteDevicesManagerInvalidTrustchainError(exc) from exc


async def get_device_invitation_creator(
    backend_cmds: APIV1_BackendAnonymousCmds, root_verify_key: VerifyKey, new_device_id: DeviceID
) -> Tuple[
    UserCertificateContent, Optional[RevokedUserCertificateContent], DeviceCertificateContent
]:
    """
    Raises:
        RemoteDevicesManagerError
        RemoteDevicesManagerBackendOfflineError
        RemoteDevicesManagerNotFoundError
        RemoteDevicesManagerInvalidTrustchainError
    """
    try:
        rep = await backend_cmds.device_get_invitation_creator(new_device_id)
    except BackendNotAvailable as exc:
        raise RemoteDevicesManagerBackendOfflineError(*exc.args) from exc
    except BackendConnectionError as exc:
        raise RemoteDevicesManagerError(
            "Failed to fetch invitation creator for device "
            f"`{new_device_id}` from the backend: {exc}"
        ) from exc

    if rep["status"] == "not_found":
        raise RemoteDevicesManagerNotFoundError(f"User `{new_device_id}` doesn't exist in backend")
    elif rep["status"] != "ok":
        raise RemoteDevicesManagerError(
            f"Cannot fetch invitation creator for device `{new_device_id}`: `{rep['status']}`"
        )

    try:
        ctx = TrustchainContext(root_verify_key, DEFAULT_CACHE_VALIDITY)
        user, _, (device,) = ctx.load_user_and_devices(
            trustchain=rep["trustchain"],
            user_certif=rep["user_certificate"],
            devices_certifs=(rep["device_certificate"],),
        )
    except TrustchainError as exc:
        raise RemoteDevicesManagerInvalidTrustchainError(exc) from exc

    return user, device


async def get_user_invitation_creator(
    backend_cmds: APIV1_BackendAnonymousCmds, root_verify_key: VerifyKey, new_user_id: DeviceID
) -> Tuple[UserCertificateContent, DeviceCertificateContent]:
    """
    Raises:
        RemoteDevicesManagerError
        RemoteDevicesManagerBackendOfflineError
        RemoteDevicesManagerNotFoundError
        RemoteDevicesManagerInvalidTrustchainError
    """
    try:
        rep = await backend_cmds.user_get_invitation_creator(new_user_id)
    except BackendNotAvailable as exc:
        raise RemoteDevicesManagerBackendOfflineError(*exc.args) from exc
    except BackendConnectionError as exc:
        raise RemoteDevicesManagerError(
            "Failed to fetch invitation creator for user "
            f"`{new_user_id}` from the backend: {exc}"
        ) from exc

    if rep["status"] == "not_found":
        raise RemoteDevicesManagerNotFoundError(f"User `{new_user_id}` doesn't exist in backend")
    elif rep["status"] != "ok":
        raise RemoteDevicesManagerError(
            f"Cannot fetch invitation creator for device `{new_user_id}`: `{rep['status']}`"
        )

    try:
        ctx = TrustchainContext(root_verify_key, DEFAULT_CACHE_VALIDITY)
        user, _, (device,) = ctx.load_user_and_devices(
            trustchain=rep["trustchain"],
            user_certif=rep["user_certificate"],
            devices_certifs=(rep["device_certificate"],),
        )
    except TrustchainError as exc:
        raise RemoteDevicesManagerInvalidTrustchainError(exc) from exc

    return user, device
