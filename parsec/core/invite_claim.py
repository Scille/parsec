# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import secrets
from typing import Optional

import pendulum
import trio

from parsec.api.data import (
    APIV1_DeviceClaimAnswerContent,
    APIV1_DeviceClaimContent,
    APIV1_UserClaimContent,
    DataError,
    DeviceCertificateContent,
    UserCertificateContent,
    UserProfile,
)
from parsec.api.protocol import DeviceID, DeviceName, UserID
from parsec.core.backend_connection import (
    BackendConnectionError,
    BackendNotAvailable,
    apiv1_backend_anonymous_cmds_factory,
    apiv1_backend_authenticated_cmds_factory,
)
from parsec.core.local_device import generate_new_device
from parsec.core.remote_devices_manager import (
    RemoteDevicesManagerBackendOfflineError,
    RemoteDevicesManagerError,
    get_device_invitation_creator,
    get_user_invitation_creator,
)
from parsec.core.types import BackendOrganizationAddr, LocalDevice
from parsec.crypto import PrivateKey, SecretKey, SigningKey

CANCEL_INVITATION_MAX_WAIT = 3


# TODO: wrap exceptions from lower layers
# TODO: handles apiv1_backend_anonymous_cmds_factory for caller (just pass backend addr)


class InviteClaimError(Exception):
    pass


class InviteClaimValidationError(InviteClaimError):
    pass


class InviteClaimPackingError(InviteClaimError):
    pass


class InviteClaimCryptoError(InviteClaimError):
    pass


class InviteClaimBackendOfflineError(InviteClaimError):
    pass


class InviteClaimTimeoutError(InviteClaimError):
    pass


class InviteClaimInvalidTokenError(InviteClaimError):
    pass


def generate_invitation_token(length=6):
    """Generate a random token of 6 digits"""
    return str(secrets.randbelow(10 ** length)).rjust(length, "0")


async def claim_user(
    organization_addr: BackendOrganizationAddr,
    new_device_id: DeviceID,
    token: str,
    keepalive: Optional[int] = None,
) -> LocalDevice:
    """
    Raises:
        InviteClaimError
        InviteClaimBackendOfflineError
        InviteClaimValidationError
        InviteClaimPackingError
        InviteClaimCryptoError
    """
    new_device = generate_new_device(new_device_id, organization_addr)

    try:
        async with apiv1_backend_anonymous_cmds_factory(
            organization_addr, keepalive=keepalive
        ) as cmds:
            # 1) Retrieve invitation creator
            try:
                invitation_creator_user, invitation_creator_device = await get_user_invitation_creator(
                    cmds, new_device.root_verify_key, new_device.user_id
                )

            except RemoteDevicesManagerBackendOfflineError as exc:
                raise InviteClaimBackendOfflineError(str(exc)) from exc

            except RemoteDevicesManagerError as exc:
                raise InviteClaimError(f"Cannot retrieve invitation creator: {exc}") from exc

            # 2) Generate claim info for invitation creator
            try:
                encrypted_claim = APIV1_UserClaimContent(
                    device_id=new_device_id,
                    token=token,
                    public_key=new_device.public_key,
                    verify_key=new_device.verify_key,
                ).dump_and_encrypt_for(recipient_pubkey=invitation_creator_user.public_key)

            except DataError as exc:
                raise InviteClaimError(f"Cannot generate user claim message: {exc}") from exc

            # 3) Send claim
            rep = await cmds.user_claim(new_device_id.user_id, encrypted_claim)
            if rep["status"] != "ok":
                raise InviteClaimError(f"Cannot claim user: {rep}")

            # 4) Verify user&device certificates and check admin status
            try:
                user = UserCertificateContent.verify_and_load(
                    rep["user_certificate"],
                    author_verify_key=invitation_creator_device.verify_key,
                    expected_author=invitation_creator_device.device_id,
                    expected_user=new_device_id.user_id,
                )

                DeviceCertificateContent.verify_and_load(
                    rep["device_certificate"],
                    author_verify_key=invitation_creator_device.verify_key,
                    expected_author=invitation_creator_device.device_id,
                    expected_device=new_device_id,
                )

                new_device = new_device.evolve(profile=user.profile)

            except DataError as exc:
                raise InviteClaimCryptoError(str(exc)) from exc

    except BackendNotAvailable as exc:
        raise InviteClaimBackendOfflineError(str(exc)) from exc

    except BackendConnectionError as exc:
        raise InviteClaimError(f"Cannot claim user: {exc}") from exc

    return new_device


async def claim_device(
    organization_addr: BackendOrganizationAddr,
    new_device_id: DeviceID,
    token: str,
    keepalive: Optional[int] = None,
) -> LocalDevice:
    """
    Raises:
        InviteClaimError
        InviteClaimBackendOfflineError
        InviteClaimValidationError
        InviteClaimPackingError
        InviteClaimCryptoError
    """
    device_signing_key = SigningKey.generate()
    answer_private_key = PrivateKey.generate()

    try:
        async with apiv1_backend_anonymous_cmds_factory(
            organization_addr, keepalive=keepalive
        ) as cmds:
            # 1) Retrieve invitation creator
            try:
                invitation_creator_user, invitation_creator_device = await get_device_invitation_creator(
                    cmds, organization_addr.root_verify_key, new_device_id
                )

            except RemoteDevicesManagerBackendOfflineError as exc:
                raise InviteClaimBackendOfflineError(str(exc)) from exc

            except RemoteDevicesManagerError as exc:
                raise InviteClaimError(f"Cannot retrieve invitation creator: {exc}") from exc

            # 2) Generate claim info for invitation creator
            try:
                encrypted_claim = APIV1_DeviceClaimContent(
                    token=token,
                    device_id=new_device_id,
                    verify_key=device_signing_key.verify_key,
                    answer_public_key=answer_private_key.public_key,
                ).dump_and_encrypt_for(recipient_pubkey=invitation_creator_user.public_key)

            except DataError as exc:
                raise InviteClaimError(f"Cannot generate device claim message: {exc}") from exc

            # 3) Send claim
            rep = await cmds.device_claim(new_device_id, encrypted_claim)
            if rep["status"] != "ok":
                raise InviteClaimError(f"Claim request error: {rep}")

            # 4) Verify device certificate
            try:
                DeviceCertificateContent.verify_and_load(
                    rep["device_certificate"],
                    author_verify_key=invitation_creator_device.verify_key,
                    expected_author=invitation_creator_device.device_id,
                    expected_device=new_device_id,
                )

            except DataError as exc:
                raise InviteClaimCryptoError(str(exc)) from exc

            try:
                answer = APIV1_DeviceClaimAnswerContent.decrypt_and_load_for(
                    rep["encrypted_answer"], recipient_privkey=answer_private_key
                )

            except DataError as exc:
                raise InviteClaimCryptoError(f"Cannot decrypt device claim answer: {exc}") from exc

    except BackendNotAvailable as exc:
        raise InviteClaimBackendOfflineError(str(exc)) from exc

    except BackendConnectionError as exc:
        raise InviteClaimError(f"Cannot claim device: {exc}") from exc

    return LocalDevice(
        organization_addr=organization_addr,
        device_id=new_device_id,
        device_label=None,
        human_handle=None,
        signing_key=device_signing_key,
        private_key=answer.private_key,
        profile=UserProfile.ADMIN if invitation_creator_user.is_admin else UserProfile.STANDARD,
        user_manifest_id=answer.user_manifest_id,
        user_manifest_key=answer.user_manifest_key,
        local_symkey=SecretKey.generate(),
    )


async def invite_and_create_device(
    device: LocalDevice, new_device_name: DeviceName, token: str, keepalive: Optional[int] = None
) -> None:
    """
    Raises:
        InviteClaimError
        InviteClaimBackendOfflineError
        InviteClaimValidationError
        InviteClaimPackingError
        InviteClaimCryptoError
        InviteClaimInvalidTokenError
    """
    try:
        async with apiv1_backend_authenticated_cmds_factory(
            device.organization_addr, device.device_id, device.signing_key, keepalive=keepalive
        ) as cmds:
            try:

                rep = await cmds.device_invite(new_device_name)
                if rep["status"] != "ok":
                    raise InviteClaimError(f"Cannot invite device: {rep}")

                try:
                    claim = APIV1_DeviceClaimContent.decrypt_and_load_for(
                        rep["encrypted_claim"], recipient_privkey=device.private_key
                    )

                except DataError as exc:
                    raise InviteClaimCryptoError(
                        f"Cannot decrypt device claim info: {exc}"
                    ) from exc

                if claim.token != token:
                    raise InviteClaimInvalidTokenError(
                        f"Invalid claim token provided by peer: `{claim.token}`"
                        f" (was expecting `{token}`)"
                    )

                try:
                    now = pendulum.now()
                    device_certificate = DeviceCertificateContent(
                        author=device.device_id,
                        timestamp=now,
                        device_id=claim.device_id,
                        device_label=None,
                        verify_key=claim.verify_key,
                    ).dump_and_sign(device.signing_key)

                except DataError as exc:
                    raise InviteClaimError(f"Cannot generate device certificate: {exc}") from exc

                try:
                    encrypted_answer = APIV1_DeviceClaimAnswerContent(
                        private_key=device.private_key,
                        user_manifest_id=device.user_manifest_id,
                        user_manifest_key=device.user_manifest_key,
                    ).dump_and_encrypt_for(recipient_pubkey=claim.answer_public_key)

                except DataError as exc:
                    raise InviteClaimError(
                        f"Cannot generate user claim answer message: {exc}"
                    ) from exc

            except:
                # Cancel the invitation to prevent the claiming peer from
                # waiting us until timeout.
                deadline = trio.current_time() + CANCEL_INVITATION_MAX_WAIT
                with trio.CancelScope(shield=True, deadline=deadline):
                    try:
                        await cmds.device_cancel_invitation(new_device_name)
                    except BackendConnectionError:
                        pass
                raise

            rep = await cmds.device_create(device_certificate, encrypted_answer)
            if rep["status"] != "ok":
                raise InviteClaimError(f"Cannot create device: {rep}")

    except BackendNotAvailable as exc:
        raise InviteClaimBackendOfflineError(str(exc)) from exc

    except BackendConnectionError as exc:
        raise InviteClaimError(f"Cannot create device: {exc}") from exc


async def invite_and_create_user(
    device: LocalDevice,
    user_id: UserID,
    token: str,
    is_admin: bool,
    keepalive: Optional[int] = None,
) -> DeviceID:
    """
    Raises:
        InviteClaimError
        InviteClaimBackendOfflineError
        InviteClaimValidationError
        InviteClaimPackingError
        InviteClaimCryptoError
        InviteClaimInvalidTokenError
    """
    try:
        async with apiv1_backend_authenticated_cmds_factory(
            device.organization_addr, device.device_id, device.signing_key, keepalive=keepalive
        ) as cmds:
            try:
                rep = await cmds.user_invite(user_id)
                if rep["status"] == "timeout":
                    raise InviteClaimTimeoutError()
                elif rep["status"] != "ok":
                    raise InviteClaimError(f"Cannot invite user: {rep}")

                try:
                    claim = APIV1_UserClaimContent.decrypt_and_load_for(
                        rep["encrypted_claim"], recipient_privkey=device.private_key
                    )

                except DataError as exc:
                    raise InviteClaimCryptoError(f"Cannot decrypt user claim info: {exc}") from exc

                if claim.token != token:
                    raise InviteClaimInvalidTokenError(
                        f"Invalid claim token provided by peer: `{claim.token}`"
                        f" (was expecting `{token}`)"
                    )

                device_id = claim.device_id
                now = pendulum.now()
                try:

                    user_certificate = UserCertificateContent(
                        author=device.device_id,
                        timestamp=now,
                        user_id=device_id.user_id,
                        human_handle=None,
                        public_key=claim.public_key,
                        profile=UserProfile.ADMIN if is_admin else UserProfile.STANDARD,
                    ).dump_and_sign(device.signing_key)
                    device_certificate = DeviceCertificateContent(
                        author=device.device_id,
                        timestamp=now,
                        device_id=device_id,
                        device_label=None,
                        verify_key=claim.verify_key,
                    ).dump_and_sign(device.signing_key)

                except DataError as exc:
                    raise InviteClaimError(
                        f"Cannot generate user&first device certificates: {exc}"
                    ) from exc

            except:
                # Cancel the invitation to prevent the claiming peer from
                # waiting us until timeout
                deadline = trio.current_time() + CANCEL_INVITATION_MAX_WAIT
                with trio.CancelScope(shield=True, deadline=deadline):
                    try:
                        await cmds.user_cancel_invitation(user_id)
                    except BackendConnectionError:
                        pass
                raise

            rep = await cmds.user_create(user_certificate, device_certificate)
            if rep["status"] != "ok":
                raise InviteClaimError(f"Cannot create user: {rep}")

    except BackendNotAvailable as exc:
        raise InviteClaimBackendOfflineError(str(exc)) from exc

    except BackendConnectionError as exc:
        raise InviteClaimError(f"Cannot create user: {exc}") from exc

    return device_id
