# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from uuid import UUID, uuid4

import pytest
from async_generator import asynccontextmanager
from pendulum import Pendulum
from pendulum import now as pendulum_now

from parsec.api.data import RealmRoleCertificateContent
from parsec.api.protocol import (
    APIV1_AdministrationClientHandshake,
    APIV1_AnonymousClientHandshake,
    APIV1_AuthenticatedClientHandshake,
    AuthenticatedClientHandshake,
    InvitationType,
    InvitedClientHandshake,
    OrganizationID,
    RealmRole,
)
from parsec.api.transport import Transport
from parsec.backend.backend_events import BackendEvent
from parsec.backend.realm import RealmGrantedRole
from parsec.core.types import LocalDevice
from tests.common import FreezeTestOnTransportError


@pytest.fixture
def backend_raw_transport_factory(server_factory):
    @asynccontextmanager
    async def _backend_sock_factory(backend, freeze_on_transport_error=True):
        async with server_factory(backend.handle_client) as server:
            stream = server.connection_factory()
            transport = await Transport.init_for_client(stream, server.addr.hostname)
            if freeze_on_transport_error:
                transport = FreezeTestOnTransportError(transport)

            yield transport

    return _backend_sock_factory


# TODO: legacy fixture, remove this when APIv1 is deprecated
@pytest.fixture
def apiv1_backend_sock_factory(backend_raw_transport_factory, coolorg):
    @asynccontextmanager
    async def _backend_sock_factory(backend, auth_as, freeze_on_transport_error=True):
        async with backend_raw_transport_factory(
            backend, freeze_on_transport_error=freeze_on_transport_error
        ) as transport:
            if auth_as:
                # Handshake
                if isinstance(auth_as, OrganizationID):
                    ch = APIV1_AnonymousClientHandshake(auth_as)
                elif auth_as == "anonymous":
                    # TODO: for legacy test, refactorise this ?
                    ch = APIV1_AnonymousClientHandshake(coolorg.organization_id)
                elif auth_as == "administration":
                    ch = APIV1_AdministrationClientHandshake(backend.config.administration_token)
                else:
                    ch = APIV1_AuthenticatedClientHandshake(
                        auth_as.organization_id,
                        auth_as.device_id,
                        auth_as.signing_key,
                        auth_as.root_verify_key,
                    )
                challenge_req = await transport.recv()
                answer_req = ch.process_challenge_req(challenge_req)
                await transport.send(answer_req)
                result_req = await transport.recv()
                ch.process_result_req(result_req)

            yield transport

    return _backend_sock_factory


@pytest.fixture
async def apiv1_anonymous_backend_sock(apiv1_backend_sock_factory, backend):
    async with apiv1_backend_sock_factory(backend, "anonymous") as sock:
        yield sock


# TODO: rename into `apiv1_administration_backend_sock`
@pytest.fixture
async def administration_backend_sock(apiv1_backend_sock_factory, backend):
    async with apiv1_backend_sock_factory(backend, "administration") as sock:
        yield sock


@pytest.fixture
async def apiv1_alice_backend_sock(apiv1_backend_sock_factory, backend, alice):
    async with apiv1_backend_sock_factory(backend, alice) as sock:
        yield sock


@pytest.fixture
async def apiv1_alice2_backend_sock(apiv1_backend_sock_factory, backend, alice2):
    async with apiv1_backend_sock_factory(backend, alice2) as sock:
        yield sock


@pytest.fixture
async def apiv1_otheralice_backend_sock(apiv1_backend_sock_factory, backend, otheralice):
    async with apiv1_backend_sock_factory(backend, otheralice) as sock:
        yield sock


@pytest.fixture
async def apiv1_adam_backend_sock(apiv1_backend_sock_factory, backend, adam):
    async with apiv1_backend_sock_factory(backend, adam) as sock:
        yield sock


@pytest.fixture
async def apiv1_bob_backend_sock(apiv1_backend_sock_factory, backend, bob):
    async with apiv1_backend_sock_factory(backend, bob) as sock:
        yield sock


@pytest.fixture
def backend_sock_factory(backend_raw_transport_factory, coolorg):
    # APIv2's invited handshake is not compatible with this
    # fixture because it requires purpose information (invitation_type/token)
    @asynccontextmanager
    async def _backend_sock_factory(backend, auth_as: LocalDevice, freeze_on_transport_error=True):
        async with backend_raw_transport_factory(
            backend, freeze_on_transport_error=freeze_on_transport_error
        ) as transport:
            # Handshake
            ch = AuthenticatedClientHandshake(
                auth_as.organization_id,
                auth_as.device_id,
                auth_as.signing_key,
                auth_as.root_verify_key,
            )
            challenge_req = await transport.recv()
            answer_req = ch.process_challenge_req(challenge_req)
            await transport.send(answer_req)
            result_req = await transport.recv()
            ch.process_result_req(result_req)

            yield transport

    return _backend_sock_factory


@pytest.fixture
async def alice_backend_sock(backend_sock_factory, backend, alice):
    async with backend_sock_factory(backend, alice) as sock:
        yield sock


@pytest.fixture
async def alice2_backend_sock(backend_sock_factory, backend, alice2):
    async with backend_sock_factory(backend, alice2) as sock:
        yield sock


@pytest.fixture
async def otheralice_backend_sock(backend_sock_factory, backend, otheralice):
    async with backend_sock_factory(backend, otheralice) as sock:
        yield sock


@pytest.fixture
async def adam_backend_sock(backend_sock_factory, backend, adam):
    async with backend_sock_factory(backend, adam) as sock:
        yield sock


@pytest.fixture
async def bob_backend_sock(backend_sock_factory, backend, bob):
    async with backend_sock_factory(backend, bob) as sock:
        yield sock


@pytest.fixture
def backend_invited_sock_factory(backend_raw_transport_factory):
    @asynccontextmanager
    async def _backend_sock_factory(
        backend,
        organization_id: OrganizationID,
        invitation_type: InvitationType,
        token: UUID,
        freeze_on_transport_error: bool = True,
    ):
        async with backend_raw_transport_factory(
            backend, freeze_on_transport_error=freeze_on_transport_error
        ) as transport:
            ch = InvitedClientHandshake(
                organization_id=organization_id, invitation_type=invitation_type, token=token
            )
            challenge_req = await transport.recv()
            answer_req = ch.process_challenge_req(challenge_req)
            await transport.send(answer_req)
            result_req = await transport.recv()
            ch.process_result_req(result_req)

            yield transport

    return _backend_sock_factory


@pytest.fixture
def realm_factory():
    async def _realm_factory(backend, author, realm_id=None, now=None):
        realm_id = realm_id or uuid4()
        now = now or pendulum_now()
        certif = RealmRoleCertificateContent.build_realm_root_certif(
            author=author.device_id, timestamp=now, realm_id=realm_id
        ).dump_and_sign(author.signing_key)
        with backend.event_bus.listen() as spy:
            await backend.realm.create(
                organization_id=author.organization_id,
                self_granted_role=RealmGrantedRole(
                    realm_id=realm_id,
                    user_id=author.user_id,
                    certificate=certif,
                    role=RealmRole.OWNER,
                    granted_by=author.device_id,
                    granted_on=now,
                ),
            )
            await spy.wait_with_timeout(BackendEvent.REALM_ROLES_UPDATED)
        return realm_id

    return _realm_factory


@pytest.fixture
async def realm(backend, alice, realm_factory):
    realm_id = UUID("A0000000000000000000000000000000")
    return await realm_factory(backend, alice, realm_id, Pendulum(2000, 1, 2))


@pytest.fixture
async def vlobs(backend, alice, realm):
    vlob_ids = (UUID("10000000000000000000000000000000"), UUID("20000000000000000000000000000000"))
    await backend.vlob.create(
        organization_id=alice.organization_id,
        author=alice.device_id,
        realm_id=realm,
        encryption_revision=1,
        vlob_id=vlob_ids[0],
        timestamp=Pendulum(2000, 1, 2),
        blob=b"r:A b:1 v:1",
    )
    await backend.vlob.update(
        organization_id=alice.organization_id,
        author=alice.device_id,
        encryption_revision=1,
        vlob_id=vlob_ids[0],
        version=2,
        timestamp=Pendulum(2000, 1, 3),
        blob=b"r:A b:1 v:2",
    )
    await backend.vlob.create(
        organization_id=alice.organization_id,
        author=alice.device_id,
        realm_id=realm,
        encryption_revision=1,
        vlob_id=vlob_ids[1],
        timestamp=Pendulum(2000, 1, 4),
        blob=b"r:A b:2 v:1",
    )
    return vlob_ids


@pytest.fixture
async def vlob_atoms(vlobs):
    return [(vlobs[0], 1), (vlobs[0], 2), (vlobs[1], 1)]


@pytest.fixture
async def other_realm(backend, alice, realm_factory):
    realm_id = UUID("B0000000000000000000000000000000")
    return await realm_factory(backend, alice, realm_id, Pendulum(2000, 1, 2))


@pytest.fixture
async def bob_realm(backend, bob, realm_factory):
    realm_id = UUID("C0000000000000000000000000000000")
    return await realm_factory(backend, bob, realm_id, Pendulum(2000, 1, 2))
