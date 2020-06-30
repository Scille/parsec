# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Optional, Tuple, Union

import attr

from parsec.api.data import (
    DataError,
    InviteDeviceConfirmation,
    InviteDeviceData,
    InviteUserConfirmation,
    InviteUserData,
    SASCode,
    generate_sas_code_candidates,
    generate_sas_codes,
)
from parsec.api.protocol import HumanHandle, InvitationType, UserID
from parsec.core.backend_connection import BackendInvitedCmds
from parsec.core.invite.exceptions import (
    InviteAlreadyUsedError,
    InviteError,
    InviteNotFoundError,
    InvitePeerResetError,
)
from parsec.core.local_device import generate_new_device
from parsec.core.types import BackendOrganizationAddr, LocalDevice
from parsec.crypto import (
    HashDigest,
    PrivateKey,
    SecretKey,
    SigningKey,
    generate_nonce,
    generate_shared_secret_key,
)


def _check_rep(rep, step_name):
    if rep["status"] == "not_found":
        raise InviteNotFoundError
    elif rep["status"] == "already_deleted":
        raise InviteAlreadyUsedError
    elif rep["status"] == "invalid_state":
        raise InvitePeerResetError
    elif rep["status"] != "ok":
        raise InviteError(f"Backend error during {step_name}: {rep}")


async def claimer_retrieve_info(
    cmds: BackendInvitedCmds
) -> Union["UserClaimInitialCtx", "DeviceClaimInitialCtx"]:
    rep = await cmds.invite_info()
    _check_rep(rep, step_name="invitation retrieval")

    if rep["type"] == InvitationType.USER:
        return UserClaimInitialCtx(
            claimer_email=rep["claimer_email"],
            greeter_user_id=rep["greeter_user_id"],
            greeter_human_handle=rep["greeter_human_handle"],
            cmds=cmds,
        )
    else:
        return DeviceClaimInitialCtx(
            greeter_user_id=rep["greeter_user_id"],
            greeter_human_handle=rep["greeter_human_handle"],
            cmds=cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BaseClaimInitialCtx:
    greeter_user_id: UserID
    greeter_human_handle: Optional[HumanHandle]

    _cmds: BackendInvitedCmds

    async def _do_wait_peer(self) -> Tuple[SASCode, SASCode, SecretKey]:
        claimer_private_key = PrivateKey.generate()
        rep = await self._cmds.invite_1_claimer_wait_peer(
            claimer_public_key=claimer_private_key.public_key
        )
        _check_rep(rep, step_name="step 1")

        shared_secret_key = generate_shared_secret_key(
            our_private_key=claimer_private_key, peer_public_key=rep["greeter_public_key"]
        )
        claimer_nonce = generate_nonce()

        rep = await self._cmds.invite_2a_claimer_send_hashed_nonce(
            claimer_hashed_nonce=HashDigest.from_data(claimer_nonce)
        )
        _check_rep(rep, step_name="step 2a")

        claimer_sas, greeter_sas = generate_sas_codes(
            claimer_nonce=claimer_nonce,
            greeter_nonce=rep["greeter_nonce"],
            shared_secret_key=shared_secret_key,
        )

        rep = await self._cmds.invite_2b_claimer_send_nonce(claimer_nonce=claimer_nonce)
        _check_rep(rep, step_name="step 2b")

        return claimer_sas, greeter_sas, shared_secret_key


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserClaimInitialCtx(BaseClaimInitialCtx):
    claimer_email: str

    async def do_wait_peer(self) -> "UserClaimInProgress1Ctx":
        claimer_sas, greeter_sas, shared_secret_key = await self._do_wait_peer()
        return UserClaimInProgress1Ctx(
            greeter_sas=greeter_sas,
            claimer_sas=claimer_sas,
            shared_secret_key=shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceClaimInitialCtx(BaseClaimInitialCtx):
    async def do_wait_peer(self) -> "DeviceClaimInProgress1Ctx":
        claimer_sas, greeter_sas, shared_secret_key = await self._do_wait_peer()
        return DeviceClaimInProgress1Ctx(
            greeter_sas=greeter_sas,
            claimer_sas=claimer_sas,
            shared_secret_key=shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BaseClaimInProgress1Ctx:
    greeter_sas: SASCode

    _claimer_sas: SASCode
    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    def generate_greeter_sas_choices(self, size: int = 3) -> List[SASCode]:
        return generate_sas_code_candidates(self.greeter_sas, size=size)

    async def _do_signify_trust(self) -> None:
        rep = await self._cmds.invite_3a_claimer_signify_trust()
        _check_rep(rep, step_name="step 3a")


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserClaimInProgress1Ctx(BaseClaimInProgress1Ctx):
    async def do_signify_trust(self) -> "UserClaimInProgress2Ctx":
        await self._do_signify_trust()
        return UserClaimInProgress2Ctx(
            claimer_sas=self._claimer_sas,
            shared_secret_key=self._shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceClaimInProgress1Ctx(BaseClaimInProgress1Ctx):
    async def do_signify_trust(self) -> "DeviceClaimInProgress2Ctx":
        await self._do_signify_trust()
        return DeviceClaimInProgress2Ctx(
            claimer_sas=self._claimer_sas,
            shared_secret_key=self._shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BaseClaimInProgress2Ctx:
    claimer_sas: SASCode

    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def _do_wait_peer_trust(self) -> None:
        rep = await self._cmds.invite_3b_claimer_wait_peer_trust()
        _check_rep(rep, step_name="step 3b")


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserClaimInProgress2Ctx(BaseClaimInProgress2Ctx):
    async def do_wait_peer_trust(self) -> "UserClaimInProgress3Ctx":
        await self._do_wait_peer_trust()
        return UserClaimInProgress3Ctx(shared_secret_key=self._shared_secret_key, cmds=self._cmds)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceClaimInProgress2Ctx(BaseClaimInProgress2Ctx):
    async def do_wait_peer_trust(self) -> "DeviceClaimInProgress3Ctx":
        await self._do_wait_peer_trust()
        return DeviceClaimInProgress3Ctx(shared_secret_key=self._shared_secret_key, cmds=self._cmds)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserClaimInProgress3Ctx:
    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def do_claim_user(
        self, requested_device_label: Optional[str], requested_human_handle: Optional[HumanHandle]
    ) -> LocalDevice:
        private_key = PrivateKey.generate()
        signing_key = SigningKey.generate()

        try:
            payload = InviteUserData(
                requested_device_label=requested_device_label,
                requested_human_handle=requested_human_handle,
                public_key=private_key.public_key,
                verify_key=signing_key.verify_key,
            ).dump_and_encrypt(key=self._shared_secret_key)
        except DataError as exc:
            raise InviteError("Cannot generate InviteUserData payload") from exc

        rep = await self._cmds.invite_4_claimer_communicate(payload=payload)
        _check_rep(rep, step_name="step 4 (data exchange)")

        rep = await self._cmds.invite_4_claimer_communicate(payload=b"")
        _check_rep(rep, step_name="step 4 (confirmation exchange)")

        try:
            confirmation = InviteUserConfirmation.decrypt_and_load(
                rep["payload"], key=self._shared_secret_key
            )
        except DataError as exc:
            raise InviteError("Invalid InviteUserConfirmation payload provided by peer") from exc

        organization_addr = BackendOrganizationAddr.build(
            backend_addr=self._cmds.addr,
            organization_id=self._cmds.addr.organization_id,
            root_verify_key=confirmation.root_verify_key,
        )

        new_device = generate_new_device(
            organization_addr=organization_addr,
            device_id=confirmation.device_id,
            device_label=confirmation.device_label,
            human_handle=confirmation.human_handle,
            profile=confirmation.profile,
            private_key=private_key,
            signing_key=signing_key,
        )

        return new_device


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceClaimInProgress3Ctx:
    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def do_claim_device(self, requested_device_label: Optional[str]) -> LocalDevice:
        signing_key = SigningKey.generate()

        try:
            payload = InviteDeviceData(
                requested_device_label=requested_device_label, verify_key=signing_key.verify_key
            ).dump_and_encrypt(key=self._shared_secret_key)
        except DataError as exc:
            raise InviteError("Cannot generate InviteDeviceData payload") from exc

        rep = await self._cmds.invite_4_claimer_communicate(payload=payload)
        _check_rep(rep, step_name="step 4 (data exchange)")

        rep = await self._cmds.invite_4_claimer_communicate(payload=b"")
        _check_rep(rep, step_name="step 4 (confirmation exchange)")

        try:
            confirmation = InviteDeviceConfirmation.decrypt_and_load(
                rep["payload"], key=self._shared_secret_key
            )
        except DataError as exc:
            raise InviteError("Invalid InviteDeviceConfirmation payload provided by peer") from exc

        organization_addr = BackendOrganizationAddr.build(
            backend_addr=self._cmds.addr,
            organization_id=self._cmds.addr.organization_id,
            root_verify_key=confirmation.root_verify_key,
        )

        return LocalDevice(
            organization_addr=organization_addr,
            device_id=confirmation.device_id,
            device_label=confirmation.device_label,
            human_handle=confirmation.human_handle,
            profile=confirmation.profile,
            private_key=confirmation.private_key,
            signing_key=signing_key,
            user_manifest_id=confirmation.user_manifest_id,
            user_manifest_key=confirmation.user_manifest_key,
            local_symkey=SecretKey.generate(),
        )
