# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Optional, Sequence, Tuple

from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data import (
    DataError,
    DeviceCertificateContent,
    RevokedUserCertificateContent,
    UserCertificateContent,
    UserProfile,
)
from parsec.api.protocol import DeviceID, UserID
from parsec.crypto import VerifyKey


class TrustchainError(Exception):
    pass


def _build_signature_path(*devices_ids):
    return " <-sign- ".join(devices_ids)


class CertifState:
    __slots__ = ("certif", "content", "verified")

    def __init__(self, certif, content, verified):
        self.certif = certif
        self.content = content
        self.verified = verified


class TrustchainContext:
    def __init__(self, root_verify_key: VerifyKey, cache_validity: int):
        self.root_verify_key = root_verify_key
        self.cache_validity = cache_validity
        self._users_cache = {}
        self._devices_cache = {}
        self._revoked_users_cache = {}

    def invalidate_user_cache(self, user_id: UserID) -> None:
        self._users_cache.pop(user_id, None)

    def get_user(self, user_id: UserID, now: Pendulum = None) -> Optional[UserCertificateContent]:
        now = now or pendulum_now()
        try:
            cached_on, verified_user = self._users_cache[user_id]
            if (now - cached_on).total_seconds() < self.cache_validity:
                return verified_user
        except KeyError:
            pass
        return None

    def get_revoked_user(
        self, user_id: UserID, now: Pendulum = None
    ) -> Optional[RevokedUserCertificateContent]:
        now = now or pendulum_now()
        try:
            cached_on, verified_revoked_user = self._revoked_users_cache[user_id]
            if (now - cached_on).total_seconds() < self.cache_validity:
                return verified_revoked_user
        except KeyError:
            pass
        return None

    def get_device(
        self, device_id: DeviceID, now: Pendulum = None
    ) -> Optional[DeviceCertificateContent]:
        now = now or pendulum_now()
        try:
            cached_on, verified_device = self._devices_cache[device_id]
            if (now - cached_on).total_seconds() < self.cache_validity:
                return verified_device
        except KeyError:
            pass
        return None

    def load_user_and_devices(
        self,
        trustchain: dict,
        user_certif: bytes,
        revoked_user_certif: Optional[bytes] = None,
        devices_certifs: Sequence[bytes] = (),
        expected_user_id: UserID = None,
    ) -> Tuple[
        UserCertificateContent,
        Optional[RevokedUserCertificateContent],
        List[DeviceCertificateContent],
    ]:
        now = pendulum_now()
        verified_users, verified_revoked_users, verified_devices = self.load_trustchain(
            users=(user_certif, *trustchain["users"]),
            revoked_users=(revoked_user_certif, *trustchain["revoked_users"])
            if revoked_user_certif
            else trustchain["revoked_users"],
            devices=(*devices_certifs, *trustchain["devices"]),
            now=now,
        )

        verified_user = verified_users[0]
        if expected_user_id and verified_user.user_id != expected_user_id:
            raise TrustchainError(
                f"Expected certificate from `{expected_user_id}` but got `{verified_user.user_id}`"
            )

        if revoked_user_certif:
            verified_revoked_user = verified_revoked_users[0]
            if expected_user_id and verified_revoked_user.user_id != expected_user_id:
                raise TrustchainError(
                    f"Expected certificate from `{expected_user_id}` but got `{verified_revoked_user.user_id}`"
                )
        else:
            verified_revoked_user = None

        verified_devices = verified_devices[: len(devices_certifs)]
        for verified_device in verified_devices:
            if expected_user_id and verified_device.device_id.user_id != expected_user_id:
                raise TrustchainError(
                    f"Expected certificate from `{expected_user_id}` but got `{verified_device.device_id}`"
                )

        return verified_user, verified_revoked_user, verified_devices

    def load_trustchain(
        self,
        users: Sequence[bytes] = (),
        revoked_users: Sequence[bytes] = (),
        devices: Sequence[bytes] = (),
        now: Pendulum = None,
    ) -> Tuple[
        List[UserCertificateContent],
        List[RevokedUserCertificateContent],
        List[DeviceCertificateContent],
    ]:
        now = now or pendulum_now()

        users_states = {}
        devices_states = {}
        revoked_users_states = {}

        # Deserialize the certificates and filter the ones we already have in cache
        try:
            for certif in devices:
                unverified_device = DeviceCertificateContent.unsecure_load(certif)
                verified_device = self.get_device(unverified_device.device_id, now)
                if verified_device:
                    devices_states[verified_device.device_id] = CertifState(
                        certif, verified_device, True
                    )
                else:
                    devices_states[unverified_device.device_id] = CertifState(
                        certif, unverified_device, False
                    )

            for certif in users:
                unverified_user = UserCertificateContent.unsecure_load(certif)
                verified_user = self.get_user(unverified_user.user_id, now)
                if verified_user:
                    users_states[verified_user.user_id] = CertifState(certif, verified_user, True)
                else:
                    users_states[unverified_user.user_id] = CertifState(
                        certif, unverified_user, False
                    )

            for certif in revoked_users:
                unverified_revoked_user = RevokedUserCertificateContent.unsecure_load(certif)
                verified_revoked_user = self.get_revoked_user(unverified_revoked_user.user_id, now)
                if verified_revoked_user:
                    revoked_users_states[verified_revoked_user.user_id] = CertifState(
                        certif, verified_revoked_user, True
                    )
                else:
                    revoked_users_states[unverified_revoked_user.user_id] = CertifState(
                        certif, unverified_revoked_user, False
                    )

        except DataError as exc:
            raise TrustchainError(f"Invalid certificate: {exc}") from exc

        def _get_eventually_verified_user(user_id):
            try:
                return users_states[user_id].content
            except KeyError:
                return None

        def _get_eventually_verified_revoked_user(user_id):
            try:
                return revoked_users_states[user_id].content
            except KeyError:
                return None

        def _verify_created_by_root(certif, certif_cls, sign_chain):
            try:
                return certif_cls.verify_and_load(
                    certif, author_verify_key=self.root_verify_key, expected_author=None
                )

            except DataError as exc:
                path = _build_signature_path(*sign_chain, "<Root Key>")
                raise TrustchainError(f"{path}: Invalid certificate: {exc}") from exc

        def _verify_created_by_device(certif, certif_cls, author_id, sign_chain):
            author_device = _recursive_verify_device(author_id, sign_chain)
            try:
                verified = certif_cls.verify_and_load(
                    certif,
                    author_verify_key=author_device.verify_key,
                    expected_author=author_device.device_id,
                )

            except DataError as exc:
                path = _build_signature_path(*sign_chain, author_id)
                raise TrustchainError(f"{path}: Invalid certificate: {exc}") from exc

            # Author is either admin or signing one of it own devices
            verified_user_id = (
                verified.device_id.user_id
                if isinstance(verified, DeviceCertificateContent)
                else verified.user_id
            )
            if author_device.device_id.user_id != verified_user_id:
                author_user = _get_eventually_verified_user(author_id.user_id)
                if not author_user:
                    path = _build_signature_path(*sign_chain, author_id)
                    raise TrustchainError(
                        f"{path}: Missing user certificate for {author_id.user_id}"
                    )
                elif author_user.profile != UserProfile.ADMIN:
                    path = _build_signature_path(*sign_chain, author_id)
                    raise TrustchainError(
                        f"{path}:  Invalid signature given {author_user.user_id} is not admin"
                    )
            # Also make sure author wasn't revoked at creation time
            author_revoked_user = _get_eventually_verified_revoked_user(author_id.user_id)
            if author_revoked_user and verified.timestamp > author_revoked_user.timestamp:
                path = _build_signature_path(*sign_chain, author_id)
                raise TrustchainError(
                    f"{path}: Signature ({verified.timestamp}) is posterior "
                    f"to user revocation ({author_revoked_user.timestamp})"
                )

            return verified

        def _recursive_verify_device(device_id, signed_children=()):
            if device_id in signed_children:
                path = _build_signature_path(*signed_children, device_id)
                raise TrustchainError(f"{path}: Invalid signature loop detected")

            try:
                state = devices_states[device_id]
            except KeyError:
                path = _build_signature_path(*signed_children, device_id)
                raise TrustchainError(f"{path}: Missing device certificate for {device_id}")

            author = state.content.author
            if author is None:
                verified_device = _verify_created_by_root(
                    state.certif, DeviceCertificateContent, sign_chain=(*signed_children, device_id)
                )
            else:
                verified_device = _verify_created_by_device(
                    state.certif,
                    DeviceCertificateContent,
                    author,
                    sign_chain=(*signed_children, device_id),
                )
            return verified_device

        def _verify_user(unverified_content, certif):
            author = unverified_content.author
            user_id = unverified_content.user_id
            if author is None:
                verified_user = _verify_created_by_root(
                    certif, UserCertificateContent, sign_chain=(f"{user_id}'s creation",)
                )
            elif author.user_id == user_id:
                raise TrustchainError(f"{user_id}: Invalid self-signed user certificate")
            else:
                verified_user = _verify_created_by_device(
                    certif, UserCertificateContent, author, sign_chain=(f"{user_id}'s creation",)
                )
            return verified_user

        def _verify_revoked_user(unverified_content, certif):
            author = unverified_content.author
            user_id = unverified_content.user_id
            if author is None:
                verified_revoked_user = _verify_created_by_root(
                    certif, RevokedUserCertificateContent, sign_chain=(f"{user_id}'s revocation",)
                )
            elif author.user_id == user_id:
                raise TrustchainError(f"{user_id}: Invalid self-signed user revocation certificate")
            else:
                verified_revoked_user = _verify_created_by_device(
                    certif,
                    RevokedUserCertificateContent,
                    author,
                    sign_chain=(f"{user_id}'s revocation",),
                )
            return verified_revoked_user

        # Verified what need to be and populate the cache with them

        for certif_state in devices_states.values():
            if not certif_state.verified:
                certif_state.content = _recursive_verify_device(certif_state.content.device_id)
        for certif_state in users_states.values():
            if not certif_state.verified:
                certif_state.content = _verify_user(certif_state.content, certif_state.certif)
        for certif_state in revoked_users_states.values():
            if not certif_state.verified:
                certif_state.content = _verify_revoked_user(
                    certif_state.content, certif_state.certif
                )

        # Finally populate the cache
        for certif_state in devices_states.values():
            if not certif_state.verified:
                self._devices_cache[certif_state.content.device_id] = (now, certif_state.content)
        for certif_state in users_states.values():
            if not certif_state.verified:
                self._users_cache[certif_state.content.user_id] = (now, certif_state.content)
        for certif_state in revoked_users_states.values():
            if not certif_state.verified:
                self._revoked_users_cache[certif_state.content.user_id] = (
                    now,
                    certif_state.content,
                )

        return (
            [state.content for state in users_states.values()],
            [state.content for state in revoked_users_states.values()],
            [state.content for state in devices_states.values()],
        )
