# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Dict, Optional, Tuple

from pendulum import now as pendulum_now

from parsec.api.protocol import (
    APIV1_HandshakeType,
    HandshakeType,
    InvitationType,
    ProtocolError,
    ServerHandshake,
)
from parsec.api.transport import Transport
from parsec.backend.client_context import (
    APIV1_AdministrationClientContext,
    APIV1_AnonymousClientContext,
    AuthenticatedClientContext,
    BaseClientContext,
    InvitedClientContext,
)
from parsec.backend.invite import DeviceInvitation, InvitationError, UserInvitation
from parsec.backend.organization import OrganizationNotFoundError
from parsec.backend.user import UserNotFoundError


async def do_handshake(
    backend, transport: Transport
) -> Tuple[Optional[BaseClientContext], Optional[Dict]]:
    try:
        handshake = ServerHandshake()
        challenge_req = handshake.build_challenge_req()
        await transport.send(challenge_req)
        answer_req = await transport.recv()

        handshake.process_answer_req(answer_req)
        if handshake.answer_type == HandshakeType.AUTHENTICATED:
            context, result_req, error_infos = await _process_authenticated_answer(
                backend, transport, handshake
            )

        elif handshake.answer_type == HandshakeType.INVITED:
            context, result_req, error_infos = await _process_invited_answer(
                backend, transport, handshake
            )

        elif handshake.answer_type == APIV1_HandshakeType.AUTHENTICATED:
            context, result_req, error_infos = await _apiv1_process_authenticated_answer(
                backend, transport, handshake
            )

        elif handshake.answer_type == APIV1_HandshakeType.ANONYMOUS:
            context, result_req, error_infos = await _apiv1_process_anonymous_answer(
                backend, transport, handshake
            )

        else:
            assert handshake.answer_type == APIV1_HandshakeType.ADMINISTRATION
            context, result_req, error_infos = await _apiv1_process_administration_answer(
                backend, transport, handshake
            )

    except ProtocolError as exc:
        context = None
        result_req = handshake.build_bad_protocol_result_req(str(exc))
        error_infos = {"reason": str(exc), "handshake_type": handshake.answer_type}

    await transport.send(result_req)

    return context, error_infos


async def _process_authenticated_answer(
    backend, transport: Transport, handshake: ServerHandshake
) -> Tuple[Optional[BaseClientContext], bytes, Optional[Dict]]:
    return await _do_process_authenticated_answer(
        backend, transport, handshake, HandshakeType.AUTHENTICATED
    )


async def _do_process_authenticated_answer(
    backend, transport: Transport, handshake: ServerHandshake, handshake_type
) -> Tuple[Optional[BaseClientContext], bytes, Optional[Dict]]:

    organization_id = handshake.answer_data["organization_id"]
    device_id = handshake.answer_data["device_id"]
    expected_rvk = handshake.answer_data["rvk"]

    def _make_error_infos(reason):
        return {
            "reason": reason,
            "handshake_type": handshake_type,
            "organization_id": organization_id,
            "device_id": device_id,
        }

    try:
        organization = await backend.organization.get(organization_id)
        user, device = await backend.user.get_user_with_device(organization_id, device_id)

    except (OrganizationNotFoundError, UserNotFoundError, KeyError) as exc:
        result_req = handshake.build_bad_identity_result_req()
        return None, result_req, _make_error_infos(str(exc))

    if organization.root_verify_key != expected_rvk:
        result_req = handshake.build_rvk_mismatch_result_req()
        return None, result_req, _make_error_infos("Bad root verify key")

    if organization.expiration_date is not None and organization.expiration_date <= pendulum_now():
        result_req = handshake.build_organization_expired_result_req()
        return None, result_req, _make_error_infos("Expired organization")

    if user.revoked_on and user.revoked_on <= pendulum_now():
        result_req = handshake.build_revoked_device_result_req()
        return None, result_req, _make_error_infos("Revoked device")

    context = AuthenticatedClientContext(
        transport=transport,
        handshake=handshake,
        organization_id=organization_id,
        device_id=device_id,
        human_handle=user.human_handle,
        profile=user.profile,
        public_key=user.public_key,
        verify_key=device.verify_key,
    )
    result_req = handshake.build_result_req(device.verify_key)
    return context, result_req, None


async def _process_invited_answer(
    backend, transport: Transport, handshake: ServerHandshake
) -> Tuple[Optional[BaseClientContext], bytes, Optional[Dict]]:
    organization_id = handshake.answer_data["organization_id"]
    invitation_type = handshake.answer_data["invitation_type"]
    token = handshake.answer_data["token"]

    def _make_error_infos(reason):
        return {
            "reason": reason,
            "handshake_type": HandshakeType.INVITED,
            "organization_id": organization_id,
            "invitation_type": invitation_type,
            "token": token,
        }

    try:
        organization = await backend.organization.get(organization_id)

    except OrganizationNotFoundError:
        result_req = handshake.build_bad_identity_result_req()
        return None, result_req, _make_error_infos("Bad organization")

    if organization.expiration_date is not None and organization.expiration_date <= pendulum_now():
        result_req = handshake.build_organization_expired_result_req()
        return None, result_req, _make_error_infos("Expired organization")

    try:
        invitation = await backend.invite.info(
            organization_id, token=handshake.answer_data["token"]
        )
    except InvitationError:
        result_req = handshake.build_bad_identity_result_req()
        return None, result_req, _make_error_infos("Bad invitation")

    if handshake.answer_data["invitation_type"] == InvitationType.USER:
        expected_invitation_type = UserInvitation
    else:  # Device
        expected_invitation_type = DeviceInvitation
    if not isinstance(invitation, expected_invitation_type):
        result_req = handshake.build_bad_identity_result_req()
        return None, result_req, _make_error_infos("Bad invitation")

    context = InvitedClientContext(
        transport, handshake, organization_id=organization_id, invitation=invitation
    )
    result_req = handshake.build_result_req()
    return context, result_req, None


async def _apiv1_process_authenticated_answer(
    backend, transport: Transport, handshake: ServerHandshake
) -> Tuple[Optional[BaseClientContext], bytes, Optional[Dict]]:
    return await _do_process_authenticated_answer(
        backend, transport, handshake, APIV1_HandshakeType.AUTHENTICATED
    )


async def _apiv1_process_anonymous_answer(
    backend, transport: Transport, handshake: ServerHandshake
) -> Tuple[Optional[BaseClientContext], bytes, Optional[Dict]]:
    organization_id = handshake.answer_data["organization_id"]
    expected_rvk = handshake.answer_data["rvk"]

    def _make_error_infos(reason):
        return {
            "reason": reason,
            "handshake_type": APIV1_HandshakeType.ANONYMOUS,
            "organization_id": organization_id,
        }

    try:
        organization = await backend.organization.get(organization_id)

    except OrganizationNotFoundError:
        result_req = handshake.build_bad_identity_result_req()
        return None, result_req, _make_error_infos("Bad organization")

    if organization.expiration_date is not None and organization.expiration_date <= pendulum_now():
        result_req = handshake.build_organization_expired_result_req()
        return None, result_req, _make_error_infos("Expired organization")

    if expected_rvk and organization.root_verify_key != expected_rvk:
        result_req = handshake.build_rvk_mismatch_result_req()
        return None, result_req, _make_error_infos("Bad root verify key")

    context = APIV1_AnonymousClientContext(transport, handshake, organization_id=organization_id)
    result_req = handshake.build_result_req()
    return context, result_req, None


async def _apiv1_process_administration_answer(
    backend, transport: Transport, handshake: ServerHandshake
) -> Tuple[Optional[BaseClientContext], bytes, Optional[Dict]]:
    if handshake.answer_data["token"] != backend.config.administration_token:
        result_req = handshake.build_bad_administration_token_result_req()
        error_infos = {"reason": "Bad token", "handshake_type": APIV1_HandshakeType.ADMINISTRATION}
        return None, result_req, error_infos

    context = APIV1_AdministrationClientContext(transport, handshake)
    result_req = handshake.build_result_req()
    return context, result_req, None
