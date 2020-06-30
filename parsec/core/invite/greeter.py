# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Optional, Tuple
from uuid import UUID

import attr
from pendulum import now as pendulum_now

from parsec.api.data import (
    DataError,
    DeviceCertificateContent,
    InviteDeviceConfirmation,
    InviteDeviceData,
    InviteUserConfirmation,
    InviteUserData,
    SASCode,
    UserCertificateContent,
    UserProfile,
    generate_sas_code_candidates,
    generate_sas_codes,
)
from parsec.api.protocol import DeviceID, DeviceName, HumanHandle, InvitationDeletedReason
from parsec.core.backend_connection import BackendInvitedCmds
from parsec.core.invite.exceptions import (
    InviteAlreadyUsedError,
    InviteError,
    InviteNotFoundError,
    InvitePeerResetError,
)
from parsec.core.types import LocalDevice
from parsec.crypto import (
    HashDigest,
    PrivateKey,
    PublicKey,
    SecretKey,
    VerifyKey,
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


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BaseGreetInitialCtx:
    token: UUID
    _cmds: BackendInvitedCmds

    async def _do_wait_peer(self) -> Tuple[SASCode, SASCode, SecretKey]:
        greeter_private_key = PrivateKey.generate()
        rep = await self._cmds.invite_1_greeter_wait_peer(
            token=self.token, greeter_public_key=greeter_private_key.public_key
        )
        if rep["status"] == "not_found":
            raise InviteNotFoundError
        elif rep["status"] == "already_deleted":
            raise InviteAlreadyUsedError()
        elif rep["status"] != "ok":
            raise InviteError(f"Backend error during step 1: {rep}")

        shared_secret_key = generate_shared_secret_key(
            our_private_key=greeter_private_key, peer_public_key=rep["claimer_public_key"]
        )
        greeter_nonce = generate_nonce()

        rep = await self._cmds.invite_2a_greeter_get_hashed_nonce(token=self.token)
        _check_rep(rep, step_name="step 2a")

        claimer_hashed_nonce = rep["claimer_hashed_nonce"]

        rep = await self._cmds.invite_2b_greeter_send_nonce(
            token=self.token, greeter_nonce=greeter_nonce
        )
        _check_rep(rep, step_name="step 2b")

        if HashDigest.from_data(rep["claimer_nonce"]) != claimer_hashed_nonce:
            raise InviteError("Invitee nonce and hashed nonce doesn't match")

        claimer_sas, greeter_sas = generate_sas_codes(
            claimer_nonce=rep["claimer_nonce"],
            greeter_nonce=greeter_nonce,
            shared_secret_key=shared_secret_key,
        )

        return claimer_sas, greeter_sas, shared_secret_key


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserGreetInitialCtx(BaseGreetInitialCtx):
    async def do_wait_peer(self) -> "UserGreetInProgress1Ctx":
        claimer_sas, greeter_sas, shared_secret_key = await self._do_wait_peer()

        return UserGreetInProgress1Ctx(
            token=self.token,
            greeter_sas=greeter_sas,
            claimer_sas=claimer_sas,
            shared_secret_key=shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceGreetInitialCtx(BaseGreetInitialCtx):
    async def do_wait_peer(self) -> "DeviceGreetInProgress1Ctx":
        claimer_sas, greeter_sas, shared_secret_key = await self._do_wait_peer()

        return DeviceGreetInProgress1Ctx(
            token=self.token,
            greeter_sas=greeter_sas,
            claimer_sas=claimer_sas,
            shared_secret_key=shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BaseGreetInProgress1Ctx:
    token: UUID
    greeter_sas: SASCode

    _claimer_sas: SASCode
    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def _do_wait_peer_trust(self) -> None:
        rep = await self._cmds.invite_3a_greeter_wait_peer_trust(token=self.token)
        _check_rep(rep, step_name="step 3b")


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserGreetInProgress1Ctx(BaseGreetInProgress1Ctx):
    async def do_wait_peer_trust(self) -> "UserGreetInProgress2Ctx":
        await self._do_wait_peer_trust()

        return UserGreetInProgress2Ctx(
            token=self.token,
            claimer_sas=self._claimer_sas,
            shared_secret_key=self._shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceGreetInProgress1Ctx(BaseGreetInProgress1Ctx):
    async def do_wait_peer_trust(self) -> "DeviceGreetInProgress2Ctx":
        await self._do_wait_peer_trust()

        return DeviceGreetInProgress2Ctx(
            token=self.token,
            claimer_sas=self._claimer_sas,
            shared_secret_key=self._shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class BaseGreetInProgress2Ctx:
    token: UUID
    claimer_sas: SASCode

    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    def generate_claimer_sas_choices(self, size: int = 3) -> List[SASCode]:
        return generate_sas_code_candidates(self.claimer_sas, size=size)

    async def _do_signify_trust(self) -> None:
        rep = await self._cmds.invite_3b_greeter_signify_trust(token=self.token)
        _check_rep(rep, step_name="step 3a")


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserGreetInProgress2Ctx(BaseGreetInProgress2Ctx):
    async def do_signify_trust(self) -> "UserGreetInProgress3Ctx":
        await self._do_signify_trust()

        return UserGreetInProgress3Ctx(
            token=self.token, shared_secret_key=self._shared_secret_key, cmds=self._cmds
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceGreetInProgress2Ctx(BaseGreetInProgress2Ctx):
    async def do_signify_trust(self) -> "DeviceGreetInProgress3Ctx":
        await self._do_signify_trust()

        return DeviceGreetInProgress3Ctx(
            token=self.token, shared_secret_key=self._shared_secret_key, cmds=self._cmds
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserGreetInProgress3Ctx:
    token: UUID

    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def do_get_claim_requests(self) -> "UserGreetInProgress4Ctx":
        rep = await self._cmds.invite_4_greeter_communicate(token=self.token, payload=b"")
        _check_rep(rep, step_name="step 4 (data exchange)")

        if rep["payload"] is None:
            raise InviteError("Missing InviteUserData payload")

        try:
            data = InviteUserData.decrypt_and_load(rep["payload"], key=self._shared_secret_key)
        except DataError as exc:
            raise InviteError("Invalid InviteUserData payload provided by peer") from exc

        return UserGreetInProgress4Ctx(
            token=self.token,
            requested_device_label=data.requested_device_label,
            requested_human_handle=data.requested_human_handle,
            public_key=data.public_key,
            verify_key=data.verify_key,
            shared_secret_key=self._shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceGreetInProgress3Ctx:
    token: UUID

    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def do_get_claim_requests(self) -> "DeviceGreetInProgress4Ctx":
        rep = await self._cmds.invite_4_greeter_communicate(token=self.token, payload=b"")
        _check_rep(rep, step_name="step 4 (data exchange)")

        if rep["payload"] is None:
            raise InviteError("Missing InviteDeviceData payload")

        try:
            data = InviteDeviceData.decrypt_and_load(rep["payload"], key=self._shared_secret_key)
        except DataError as exc:
            raise InviteError("Invalid InviteDeviceData payload provided by peer") from exc

        return DeviceGreetInProgress4Ctx(
            token=self.token,
            requested_device_label=data.requested_device_label,
            verify_key=data.verify_key,
            shared_secret_key=self._shared_secret_key,
            cmds=self._cmds,
        )


@attr.s(slots=True, frozen=True, auto_attribs=True)
class UserGreetInProgress4Ctx:
    token: UUID
    requested_device_label: Optional[str]
    requested_human_handle: Optional[HumanHandle]

    _public_key: PublicKey
    _verify_key: VerifyKey
    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def do_create_new_user(
        self,
        author: LocalDevice,
        device_label: Optional[str],
        human_handle: Optional[HumanHandle],
        profile: UserProfile,
    ) -> None:
        device_id = DeviceID.new()
        try:
            now = pendulum_now()

            user_certificate = UserCertificateContent(
                author=author.device_id,
                timestamp=now,
                user_id=device_id.user_id,
                human_handle=human_handle,
                public_key=self._public_key,
                profile=profile,
            )
            redacted_user_certificate = user_certificate.evolve(human_handle=None)

            device_certificate = DeviceCertificateContent(
                author=author.device_id,
                timestamp=now,
                device_id=device_id,
                device_label=device_label,
                verify_key=self._verify_key,
            )
            redacted_device_certificate = device_certificate.evolve(device_label=None)

            user_certificate = user_certificate.dump_and_sign(author.signing_key)
            redacted_user_certificate = redacted_user_certificate.dump_and_sign(author.signing_key)
            device_certificate = device_certificate.dump_and_sign(author.signing_key)
            redacted_device_certificate = redacted_device_certificate.dump_and_sign(
                author.signing_key
            )

        except DataError as exc:
            raise InviteError(f"Cannot generate device certificate: {exc}") from exc

        rep = await self._cmds.user_create(
            user_certificate=user_certificate,
            device_certificate=device_certificate,
            redacted_user_certificate=redacted_user_certificate,
            redacted_device_certificate=redacted_device_certificate,
        )
        if rep["status"] != "ok":
            raise InviteError(f"Cannot create device: {rep}")

        try:
            payload = InviteUserConfirmation(
                device_id=device_id,
                device_label=device_label,
                human_handle=human_handle,
                profile=profile,
                root_verify_key=author.root_verify_key,
            ).dump_and_encrypt(key=self._shared_secret_key)
        except DataError as exc:
            raise InviteError("Cannot generate InviteUserConfirmation payload") from exc

        rep = await self._cmds.invite_4_greeter_communicate(token=self.token, payload=payload)
        _check_rep(rep, step_name="step 4 (confirmation exchange)")

        await self._cmds.invite_delete(token=self.token, reason=InvitationDeletedReason.FINISHED)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class DeviceGreetInProgress4Ctx:
    token: UUID
    requested_device_label: Optional[str]

    _verify_key: VerifyKey
    _shared_secret_key: SecretKey
    _cmds: BackendInvitedCmds

    async def do_create_new_device(self, author: LocalDevice, device_label: Optional[str]) -> None:
        device_id = author.user_id.to_device_id(DeviceName.new())
        try:
            now = pendulum_now()

            device_certificate = DeviceCertificateContent(
                author=author.device_id,
                timestamp=now,
                device_id=device_id,
                device_label=device_label,
                verify_key=self._verify_key,
            )
            redacted_device_certificate = device_certificate.evolve(device_label=None)

            device_certificate = device_certificate.dump_and_sign(author.signing_key)
            redacted_device_certificate = redacted_device_certificate.dump_and_sign(
                author.signing_key
            )

        except DataError as exc:
            raise InviteError(f"Cannot generate device certificate: {exc}") from exc

        rep = await self._cmds.device_create(
            device_certificate=device_certificate,
            redacted_device_certificate=redacted_device_certificate,
        )
        _check_rep(rep, step_name="device creation")

        try:
            payload = InviteDeviceConfirmation(
                device_id=device_id,
                device_label=device_label,
                human_handle=author.human_handle,
                profile=author.profile,
                private_key=author.private_key,
                user_manifest_id=author.user_manifest_id,
                user_manifest_key=author.user_manifest_key,
                root_verify_key=author.root_verify_key,
            ).dump_and_encrypt(key=self._shared_secret_key)
        except DataError as exc:
            raise InviteError("Cannot generate InviteUserConfirmation payload") from exc

        rep = await self._cmds.invite_4_greeter_communicate(token=self.token, payload=payload)
        _check_rep(rep, step_name="step 4 (confirmation exchange)")

        await self._cmds.invite_delete(token=self.token, reason=InvitationDeletedReason.FINISHED)
