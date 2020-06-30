# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Dict, List, Optional, Tuple
from uuid import UUID

import pendulum
from pendulum import Pendulum

from parsec.api.protocol import (
    DeviceID,
    DeviceName,
    InvitationDeletedReason,
    InvitationType,
    OrganizationID,
    ProtocolError,
    UserID,
    apiv1_device_cancel_invitation_serializer,
    apiv1_device_claim_serializer,
    apiv1_device_create_serializer,
    apiv1_device_get_invitation_creator_serializer,
    apiv1_device_invite_serializer,
    apiv1_organization_bootstrap_serializer,
    apiv1_organization_create_serializer,
    apiv1_organization_stats_serializer,
    apiv1_organization_status_serializer,
    apiv1_organization_update_serializer,
    apiv1_user_cancel_invitation_serializer,
    apiv1_user_claim_serializer,
    apiv1_user_create_serializer,
    apiv1_user_find_serializer,
    apiv1_user_get_invitation_creator_serializer,
    apiv1_user_invite_serializer,
    block_create_serializer,
    block_read_serializer,
    device_create_serializer,
    events_listen_serializer,
    events_subscribe_serializer,
    human_find_serializer,
    invite_1_claimer_wait_peer_serializer,
    invite_1_greeter_wait_peer_serializer,
    invite_2a_claimer_send_hashed_nonce_serializer,
    invite_2a_greeter_get_hashed_nonce_serializer,
    invite_2b_claimer_send_nonce_serializer,
    invite_2b_greeter_send_nonce_serializer,
    invite_3a_claimer_signify_trust_serializer,
    invite_3a_greeter_wait_peer_trust_serializer,
    invite_3b_claimer_wait_peer_trust_serializer,
    invite_3b_greeter_signify_trust_serializer,
    invite_4_claimer_communicate_serializer,
    invite_4_greeter_communicate_serializer,
    invite_delete_serializer,
    invite_info_serializer,
    invite_list_serializer,
    invite_new_serializer,
    message_get_serializer,
    ping_serializer,
    realm_create_serializer,
    realm_finish_reencryption_maintenance_serializer,
    realm_get_role_certificates_serializer,
    realm_start_reencryption_maintenance_serializer,
    realm_status_serializer,
    realm_update_roles_serializer,
    user_create_serializer,
    user_get_serializer,
    user_revoke_serializer,
    vlob_create_serializer,
    vlob_list_versions_serializer,
    vlob_maintenance_get_reencryption_batch_serializer,
    vlob_maintenance_save_reencryption_batch_serializer,
    vlob_poll_changes_serializer,
    vlob_read_serializer,
    vlob_update_serializer,
)
from parsec.api.transport import Transport, TransportError
from parsec.core.backend_connection.exceptions import BackendNotAvailable, BackendProtocolError
from parsec.core.types import EntryID
from parsec.crypto import PublicKey, VerifyKey


async def _send_cmd(transport: Transport, serializer, **req) -> dict:
    """
    Raises:
        Backend
        BackendNotAvailable
        BackendProtocolError

        BackendCmdsInvalidRequest
        BackendCmdsInvalidResponse
        BackendNotAvailable
        BackendCmdsBadResponse
    """
    transport.logger.info("Request", cmd=req["cmd"])

    try:
        raw_req = serializer.req_dumps(req)

    except ProtocolError as exc:
        transport.logger.exception("Invalid request data", cmd=req["cmd"], error=exc)
        raise BackendProtocolError("Invalid request data") from exc

    try:
        await transport.send(raw_req)
        raw_rep = await transport.recv()

    except TransportError as exc:
        transport.logger.debug("Request failed (backend not available)", cmd=req["cmd"])
        raise BackendNotAvailable(exc) from exc

    try:
        rep = serializer.rep_loads(raw_rep)

    except ProtocolError as exc:
        transport.logger.exception("Invalid response data", cmd=req["cmd"], error=exc)
        raise BackendProtocolError("Invalid response data") from exc

    if rep["status"] == "invalid_msg_format":
        transport.logger.error("Invalid request data according to backend", cmd=req["cmd"], rep=rep)
        raise BackendProtocolError("Invalid request data according to backend")

    return rep


###  Backend authenticated cmds  ###


### Events&misc API ###


async def ping(transport: Transport, ping: str = "") -> dict:
    return await _send_cmd(transport, ping_serializer, cmd="ping", ping=ping)


async def events_subscribe(transport: Transport,) -> dict:
    return await _send_cmd(transport, events_subscribe_serializer, cmd="events_subscribe")


async def events_listen(transport: Transport, wait: bool = True) -> dict:
    return await _send_cmd(transport, events_listen_serializer, cmd="events_listen", wait=wait)


### Message API ###


async def message_get(transport: Transport, offset: int) -> dict:
    return await _send_cmd(transport, message_get_serializer, cmd="message_get", offset=offset)


### Vlob API ###


async def vlob_create(
    transport: Transport,
    realm_id: UUID,
    encryption_revision: int,
    vlob_id: UUID,
    timestamp: pendulum.Pendulum,
    blob: bytes,
) -> dict:
    return await _send_cmd(
        transport,
        vlob_create_serializer,
        cmd="vlob_create",
        realm_id=realm_id,
        encryption_revision=encryption_revision,
        vlob_id=vlob_id,
        timestamp=timestamp,
        blob=blob,
    )


async def vlob_read(
    transport: Transport,
    encryption_revision: int,
    vlob_id: UUID,
    version: int = None,
    timestamp: pendulum.Pendulum = None,
) -> dict:
    return await _send_cmd(
        transport,
        vlob_read_serializer,
        cmd="vlob_read",
        encryption_revision=encryption_revision,
        vlob_id=vlob_id,
        version=version,
        timestamp=timestamp,
    )


async def vlob_update(
    transport: Transport,
    encryption_revision: int,
    vlob_id: UUID,
    version: int,
    timestamp: pendulum.Pendulum,
    blob: bytes,
) -> dict:
    return await _send_cmd(
        transport,
        vlob_update_serializer,
        cmd="vlob_update",
        encryption_revision=encryption_revision,
        vlob_id=vlob_id,
        version=version,
        timestamp=timestamp,
        blob=blob,
    )


async def vlob_poll_changes(transport: Transport, realm_id: UUID, last_checkpoint: int) -> dict:
    return await _send_cmd(
        transport,
        vlob_poll_changes_serializer,
        cmd="vlob_poll_changes",
        realm_id=realm_id,
        last_checkpoint=last_checkpoint,
    )


async def vlob_list_versions(transport: Transport, vlob_id: UUID) -> dict:
    return await _send_cmd(
        transport, vlob_list_versions_serializer, cmd="vlob_list_versions", vlob_id=vlob_id
    )


async def vlob_maintenance_get_reencryption_batch(
    transport: Transport, realm_id: UUID, encryption_revision: int, size: int
) -> dict:
    return await _send_cmd(
        transport,
        vlob_maintenance_get_reencryption_batch_serializer,
        cmd="vlob_maintenance_get_reencryption_batch",
        realm_id=realm_id,
        encryption_revision=encryption_revision,
        size=size,
    )


async def vlob_maintenance_save_reencryption_batch(
    transport: Transport,
    realm_id: UUID,
    encryption_revision: int,
    batch: List[Tuple[EntryID, int, bytes]],
) -> dict:
    return await _send_cmd(
        transport,
        vlob_maintenance_save_reencryption_batch_serializer,
        cmd="vlob_maintenance_save_reencryption_batch",
        realm_id=realm_id,
        encryption_revision=encryption_revision,
        batch=[{"vlob_id": x[0], "version": x[1], "blob": x[2]} for x in batch],
    )


### Realm API ###


async def realm_create(transport: Transport, role_certificate: bytes) -> dict:
    return await _send_cmd(
        transport, realm_create_serializer, cmd="realm_create", role_certificate=role_certificate
    )


async def realm_status(transport: Transport, realm_id: UUID) -> dict:
    return await _send_cmd(
        transport, realm_status_serializer, cmd="realm_status", realm_id=realm_id
    )


async def realm_get_role_certificates(transport: Transport, realm_id: UUID) -> dict:
    return await _send_cmd(
        transport,
        realm_get_role_certificates_serializer,
        cmd="realm_get_role_certificates",
        realm_id=realm_id,
    )


async def realm_update_roles(
    transport: Transport, role_certificate: bytes, recipient_message: bytes
) -> dict:
    return await _send_cmd(
        transport,
        realm_update_roles_serializer,
        cmd="realm_update_roles",
        role_certificate=role_certificate,
        recipient_message=recipient_message,
    )


async def realm_start_reencryption_maintenance(
    transport: Transport,
    realm_id: UUID,
    encryption_revision: int,
    timestamp: pendulum.Pendulum,
    per_participant_message: Dict[UserID, bytes],
) -> dict:
    return await _send_cmd(
        transport,
        realm_start_reencryption_maintenance_serializer,
        cmd="realm_start_reencryption_maintenance",
        realm_id=realm_id,
        encryption_revision=encryption_revision,
        timestamp=timestamp,
        per_participant_message=per_participant_message,
    )


async def realm_finish_reencryption_maintenance(
    transport: Transport, realm_id: UUID, encryption_revision: int
) -> dict:
    return await _send_cmd(
        transport,
        realm_finish_reencryption_maintenance_serializer,
        cmd="realm_finish_reencryption_maintenance",
        realm_id=realm_id,
        encryption_revision=encryption_revision,
    )


### Block API ###


async def block_create(transport: Transport, block_id: UUID, realm_id: UUID, block: bytes) -> dict:
    return await _send_cmd(
        transport,
        block_create_serializer,
        cmd="block_create",
        block_id=block_id,
        realm_id=realm_id,
        block=block,
    )


async def block_read(transport: Transport, block_id: UUID) -> dict:
    return await _send_cmd(transport, block_read_serializer, cmd="block_read", block_id=block_id)


### Invite API ###


async def invite_new(
    transport: Transport, type: InvitationType, send_email: bool = False, claimer_email: str = None
):
    return await _send_cmd(
        transport,
        invite_new_serializer,
        cmd="invite_new",
        type=type,
        send_email=send_email,
        claimer_email=claimer_email,
    )


async def invite_list(transport: Transport):
    return await _send_cmd(transport, invite_list_serializer, cmd="invite_list")


async def invite_delete(transport: Transport, token: UUID, reason: InvitationDeletedReason):
    return await _send_cmd(
        transport, invite_delete_serializer, cmd="invite_delete", token=token, reason=reason
    )


async def invite_info(transport: Transport):
    return await _send_cmd(transport, invite_info_serializer, cmd="invite_info")


async def invite_1_claimer_wait_peer(transport: Transport, claimer_public_key: PublicKey):
    return await _send_cmd(
        transport,
        invite_1_claimer_wait_peer_serializer,
        cmd="invite_1_claimer_wait_peer",
        claimer_public_key=claimer_public_key,
    )


async def invite_1_greeter_wait_peer(
    transport: Transport, token: UUID, greeter_public_key: PublicKey
):
    return await _send_cmd(
        transport,
        invite_1_greeter_wait_peer_serializer,
        cmd="invite_1_greeter_wait_peer",
        token=token,
        greeter_public_key=greeter_public_key,
    )


async def invite_2a_claimer_send_hashed_nonce(transport: Transport, claimer_hashed_nonce: bytes):
    return await _send_cmd(
        transport,
        invite_2a_claimer_send_hashed_nonce_serializer,
        cmd="invite_2a_claimer_send_hashed_nonce",
        claimer_hashed_nonce=claimer_hashed_nonce,
    )


async def invite_2a_greeter_get_hashed_nonce(transport: Transport, token: UUID):
    return await _send_cmd(
        transport,
        invite_2a_greeter_get_hashed_nonce_serializer,
        cmd="invite_2a_greeter_get_hashed_nonce",
        token=token,
    )


async def invite_2b_greeter_send_nonce(transport: Transport, token: UUID, greeter_nonce: bytes):
    return await _send_cmd(
        transport,
        invite_2b_greeter_send_nonce_serializer,
        cmd="invite_2b_greeter_send_nonce",
        token=token,
        greeter_nonce=greeter_nonce,
    )


async def invite_2b_claimer_send_nonce(transport: Transport, claimer_nonce: bytes):
    return await _send_cmd(
        transport,
        invite_2b_claimer_send_nonce_serializer,
        cmd="invite_2b_claimer_send_nonce",
        claimer_nonce=claimer_nonce,
    )


async def invite_3a_greeter_wait_peer_trust(transport: Transport, token: UUID):
    return await _send_cmd(
        transport,
        invite_3a_greeter_wait_peer_trust_serializer,
        cmd="invite_3a_greeter_wait_peer_trust",
        token=token,
    )


async def invite_3a_claimer_signify_trust(transport: Transport):
    return await _send_cmd(
        transport, invite_3a_claimer_signify_trust_serializer, cmd="invite_3a_claimer_signify_trust"
    )


async def invite_3b_claimer_wait_peer_trust(transport: Transport):
    return await _send_cmd(
        transport,
        invite_3b_claimer_wait_peer_trust_serializer,
        cmd="invite_3b_claimer_wait_peer_trust",
    )


async def invite_3b_greeter_signify_trust(transport: Transport, token: UUID):
    return await _send_cmd(
        transport,
        invite_3b_greeter_signify_trust_serializer,
        cmd="invite_3b_greeter_signify_trust",
        token=token,
    )


async def invite_4_greeter_communicate(transport: Transport, token: UUID, payload: Optional[bytes]):
    return await _send_cmd(
        transport,
        invite_4_greeter_communicate_serializer,
        cmd="invite_4_greeter_communicate",
        token=token,
        payload=payload,
    )


async def invite_4_claimer_communicate(transport: Transport, payload: Optional[bytes]):
    return await _send_cmd(
        transport,
        invite_4_claimer_communicate_serializer,
        cmd="invite_4_claimer_communicate",
        payload=payload,
    )


### User API ###


async def user_get(transport: Transport, user_id: UserID) -> dict:
    return await _send_cmd(transport, user_get_serializer, cmd="user_get", user_id=user_id)


async def apiv1_user_find(
    transport: Transport,
    query: str = None,
    page: int = 1,
    per_page: int = 100,
    omit_revoked: bool = False,
) -> dict:
    return await _send_cmd(
        transport,
        apiv1_user_find_serializer,
        cmd="user_find",
        query=query,
        page=page,
        per_page=per_page,
        omit_revoked=omit_revoked,
    )


async def human_find(
    transport: Transport,
    query: str = None,
    page: int = 1,
    per_page: int = 100,
    omit_revoked: bool = False,
    omit_non_human: bool = False,
) -> dict:
    return await _send_cmd(
        transport,
        human_find_serializer,
        cmd="human_find",
        query=query,
        page=page,
        per_page=per_page,
        omit_revoked=omit_revoked,
        omit_non_human=omit_non_human,
    )


async def user_invite(transport: Transport, user_id: UserID) -> dict:
    return await _send_cmd(
        transport, apiv1_user_invite_serializer, cmd="user_invite", user_id=user_id
    )


async def user_cancel_invitation(transport: Transport, user_id: UserID) -> dict:
    return await _send_cmd(
        transport,
        apiv1_user_cancel_invitation_serializer,
        cmd="user_cancel_invitation",
        user_id=user_id,
    )


async def user_create(
    transport: Transport,
    user_certificate: bytes,
    device_certificate: bytes,
    redacted_user_certificate: bytes,
    redacted_device_certificate: bytes,
) -> dict:
    return await _send_cmd(
        transport,
        user_create_serializer,
        cmd="user_create",
        user_certificate=user_certificate,
        device_certificate=device_certificate,
        redacted_user_certificate=redacted_user_certificate,
        redacted_device_certificate=redacted_device_certificate,
    )


async def apiv1_user_create(
    transport: Transport, user_certificate: bytes, device_certificate: bytes
) -> dict:
    return await _send_cmd(
        transport,
        apiv1_user_create_serializer,
        cmd="user_create",
        user_certificate=user_certificate,
        device_certificate=device_certificate,
    )


async def user_revoke(transport: Transport, revoked_user_certificate: bytes) -> dict:
    return await _send_cmd(
        transport,
        user_revoke_serializer,
        cmd="user_revoke",
        revoked_user_certificate=revoked_user_certificate,
    )


async def device_invite(transport: Transport, invited_device_name: DeviceName) -> dict:
    return await _send_cmd(
        transport,
        apiv1_device_invite_serializer,
        cmd="device_invite",
        invited_device_name=invited_device_name,
    )


async def device_cancel_invitation(transport: Transport, invited_device_name: DeviceName) -> dict:
    return await _send_cmd(
        transport,
        apiv1_device_cancel_invitation_serializer,
        cmd="device_cancel_invitation",
        invited_device_name=invited_device_name,
    )


async def device_create(
    transport: Transport, device_certificate: bytes, redacted_device_certificate: bytes
) -> dict:
    return await _send_cmd(
        transport,
        device_create_serializer,
        cmd="device_create",
        device_certificate=device_certificate,
        redacted_device_certificate=redacted_device_certificate,
    )


async def apiv1_device_create(
    transport: Transport, device_certificate: bytes, encrypted_answer: bytes
) -> dict:
    return await _send_cmd(
        transport,
        apiv1_device_create_serializer,
        cmd="device_create",
        device_certificate=device_certificate,
        encrypted_answer=encrypted_answer,
    )


###  Backend anonymous cmds  ###


# ping already defined in authenticated part


async def organization_create(
    transport: Transport, organization_id: OrganizationID, expiration_date: Pendulum = None
) -> dict:
    return await _send_cmd(
        transport,
        apiv1_organization_create_serializer,
        cmd="organization_create",
        organization_id=organization_id,
        expiration_date=expiration_date,
    )


async def organization_stats(transport: Transport, organization_id: OrganizationID) -> dict:
    return await _send_cmd(
        transport,
        apiv1_organization_stats_serializer,
        cmd="organization_stats",
        organization_id=organization_id,
    )


async def organization_status(transport: Transport, organization_id: OrganizationID) -> dict:
    return await _send_cmd(
        transport,
        apiv1_organization_status_serializer,
        cmd="organization_status",
        organization_id=organization_id,
    )


async def organization_update(
    transport: Transport, organization_id: OrganizationID, expiration_date: Pendulum = None
) -> dict:
    return await _send_cmd(
        transport,
        apiv1_organization_update_serializer,
        cmd="organization_update",
        organization_id=organization_id,
        expiration_date=expiration_date,
    )


async def organization_bootstrap(
    transport: Transport,
    organization_id: OrganizationID,
    bootstrap_token: str,
    root_verify_key: VerifyKey,
    user_certificate: bytes,
    device_certificate: bytes,
    redacted_user_certificate: bytes,
    redacted_device_certificate: bytes,
) -> dict:
    return await _send_cmd(
        transport,
        apiv1_organization_bootstrap_serializer,
        cmd="organization_bootstrap",
        organization_id=organization_id,
        bootstrap_token=bootstrap_token,
        root_verify_key=root_verify_key,
        user_certificate=user_certificate,
        device_certificate=device_certificate,
        redacted_user_certificate=redacted_user_certificate,
        redacted_device_certificate=redacted_device_certificate,
    )


async def user_get_invitation_creator(transport: Transport, invited_user_id: UserID) -> dict:
    return await _send_cmd(
        transport,
        apiv1_user_get_invitation_creator_serializer,
        cmd="user_get_invitation_creator",
        invited_user_id=invited_user_id,
    )


async def user_claim(transport: Transport, invited_user_id: UserID, encrypted_claim: bytes) -> dict:
    return await _send_cmd(
        transport,
        apiv1_user_claim_serializer,
        cmd="user_claim",
        invited_user_id=invited_user_id,
        encrypted_claim=encrypted_claim,
    )


async def device_get_invitation_creator(transport: Transport, invited_device_id: DeviceID) -> dict:
    return await _send_cmd(
        transport,
        apiv1_device_get_invitation_creator_serializer,
        cmd="device_get_invitation_creator",
        invited_device_id=invited_device_id,
    )


async def device_claim(
    transport: Transport, invited_device_id: DeviceID, encrypted_claim: bytes
) -> dict:
    return await _send_cmd(
        transport,
        apiv1_device_claim_serializer,
        cmd="device_claim",
        invited_device_id=invited_device_id,
        encrypted_claim=encrypted_claim,
    )
