# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional

from pendulum import DateTime

from parsec.api.protocol import OrganizationID
from parsec.crypto import VerifyKey
from parsec.backend.user import UserError, User, Device
from parsec.backend.organization import (
    BaseOrganizationComponent,
    Organization,
    OrganizationAlreadyExistsError,
    OrganizationInvalidBootstrapTokenError,
    OrganizationAlreadyBootstrappedError,
    OrganizationNotFoundError,
    OrganizationFirstUserCreationError,
)
from parsec.backend import memory
from parsec.backend.events import BackendEvent


class MemoryOrganizationComponent(BaseOrganizationComponent):
    def __init__(self, send_event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_component: "memory.MemoryUserComponent"  # Defined in `register_components`
        self._vlob_component: "memory.MemoryVlobComponent"  # Defined in `register_components`
        self._block_component: "memory.MemoryBlockComponent"  # Defined in `register_components`
        self._organizations = {}
        self._send_event = send_event

    def register_components(
        self,
        user: "memory.MemoryUserComponent",
        vlob: "memory.MemoryVlobComponent",
        block: "memory.MemoryBlockComponent",
        **other_components
    ):
        self._user_component = user
        self._vlob_component = vlob
        self._block_component = block

    async def create(
        self, id: OrganizationID, bootstrap_token: str, expiration_date: Optional[DateTime] = None
    ) -> None:
        org = self._organizations.get(id)

        # Allow overwritting of not-yet-bootstrapped organization
        if org and org.root_verify_key:
            raise OrganizationAlreadyExistsError()

        self._organizations[id] = Organization(
            organization_id=id, bootstrap_token=bootstrap_token, expiration_date=expiration_date
        )

    async def get(self, id: OrganizationID) -> Organization:
        if id not in self._organizations:
            raise OrganizationNotFoundError()
        return self._organizations[id]

    async def bootstrap(
        self,
        id: OrganizationID,
        user: User,
        first_device: Device,
        bootstrap_token: str,
        root_verify_key: VerifyKey,
    ) -> None:
        organization = await self.get(id)
        if organization.is_bootstrapped():
            raise OrganizationAlreadyBootstrappedError()

        if organization.bootstrap_token != bootstrap_token:
            raise OrganizationInvalidBootstrapTokenError()

        try:
            await self._user_component.create_user(id, user, first_device)

        except UserError as exc:
            raise OrganizationFirstUserCreationError(exc) from exc

        self._organizations[organization.organization_id] = organization.evolve(
            root_verify_key=root_verify_key
        )

    async def set_expiration_date(
        self, id: OrganizationID, expiration_date: DateTime = None
    ) -> None:
        try:
            self._organizations[id] = self._organizations[id].evolve(
                expiration_date=expiration_date
            )
            if self._organizations[id].is_expired:
                await self._send_event(BackendEvent.ORGANIZATION_EXPIRED, organization_id=id)
        except KeyError:
            raise OrganizationNotFoundError()
