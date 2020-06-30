# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional

from pendulum import Pendulum

from parsec.api.protocol import OrganizationID
from parsec.backend.memory.block import MemoryBlockComponent
from parsec.backend.memory.vlob import MemoryVlobComponent
from parsec.backend.organization import (
    BaseOrganizationComponent,
    Organization,
    OrganizationAlreadyBootstrappedError,
    OrganizationAlreadyExistsError,
    OrganizationFirstUserCreationError,
    OrganizationInvalidBootstrapTokenError,
    OrganizationNotFoundError,
    OrganizationStats,
)
from parsec.backend.user import BaseUserComponent, Device, User, UserError
from parsec.crypto import VerifyKey


class MemoryOrganizationComponent(BaseOrganizationComponent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._user_component = None
        self._vlob_component = None
        self._block_component = None
        self._organizations = {}

    def register_components(
        self,
        user: BaseUserComponent,
        vlob: MemoryVlobComponent,
        block: MemoryBlockComponent,
        **other_components
    ):
        self._user_component = user
        self._vlob_component = vlob
        self._block_component = block

    async def create(
        self, id: OrganizationID, bootstrap_token: str, expiration_date: Optional[Pendulum] = None
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

    async def stats(self, id: OrganizationID) -> OrganizationStats:
        await self.get(id)

        metadata_size = 0
        for (vlob_organization_id, _), vlob in self._vlob_component._vlobs.items():
            if vlob_organization_id == id:
                metadata_size += sum(len(blob) for (blob, _, _) in vlob.data)

        data_size = 0
        for (vlob_organization_id, _), blockmeta in self._block_component._blockmetas.items():
            if vlob_organization_id == id:
                data_size += blockmeta.size

        users = len(self._user_component._organizations[id].users)

        return OrganizationStats(users=users, data_size=data_size, metadata_size=metadata_size)

    async def set_expiration_date(
        self, id: OrganizationID, expiration_date: Pendulum = None
    ) -> None:
        try:
            self._organizations[id] = self._organizations[id].evolve(
                expiration_date=expiration_date
            )
        except KeyError:
            raise OrganizationNotFoundError()
