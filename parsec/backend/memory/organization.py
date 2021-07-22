# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from typing import Optional, Union

from pendulum import DateTime

from parsec.api.protocol import OrganizationID
from parsec.crypto import VerifyKey
from parsec.backend.user import BaseUserComponent, UserError, User, Device
from parsec.backend.organization import (
    BaseOrganizationComponent,
    Organization,
    OrganizationStats,
    OrganizationAlreadyExistsError,
    OrganizationInvalidBootstrapTokenError,
    OrganizationAlreadyBootstrappedError,
    OrganizationNotFoundError,
    OrganizationFirstUserCreationError,
)
from parsec.backend.utils import Unset, UnsetType
from parsec.backend.memory.vlob import MemoryVlobComponent
from parsec.backend.memory.block import MemoryBlockComponent
from parsec.backend.memory.realm import MemoryRealmComponent
from parsec.backend.events import BackendEvent


class MemoryOrganizationComponent(BaseOrganizationComponent):
    def __init__(self, send_event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_component = None
        self._vlob_component = None
        self._block_component = None
        self._realm_component = None
        self._organizations = {}
        self._send_event = send_event

    def register_components(
        self,
        user: BaseUserComponent,
        vlob: MemoryVlobComponent,
        block: MemoryBlockComponent,
        realm: MemoryRealmComponent,
        **other_components
    ):
        self._user_component = user
        self._vlob_component = vlob
        self._block_component = block
        self._realm_component = realm

    async def create(
        self,
        id: OrganizationID,
        bootstrap_token: str,
        expiration_date: Union[UnsetType, Optional[DateTime]] = Unset,
        active_users_limit: Optional[int] = None,
    ) -> None:
        org = self._organizations.get(id)
        # Allow overwritting of not-yet-bootstrapped organization
        if org and org.root_verify_key:
            raise OrganizationAlreadyExistsError()
        self._organizations[id] = Organization(
            organization_id=id,
            bootstrap_token=bootstrap_token,
            expiration_date=expiration_date,
            active_users_limit=active_users_limit,
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

        workspaces = len(
            [
                realm_id
                for organization_id, realm_id in self._realm_component._realms.keys()
                if organization_id == id
            ]
        )

        return OrganizationStats(
            users=users, data_size=data_size, metadata_size=metadata_size, workspaces=workspaces
        )

    async def update(
        self,
        id: OrganizationID,
        expiration_date: Union[UnsetType, Optional[DateTime]] = Unset,
        user_profile_outsider_allowed: Union[UnsetType, bool] = Unset,
        active_users_limit: Union[UnsetType, int] = Unset,
    ) -> None:
        """
        Raises:
            OrganizationNotFoundError
        """
        if id not in self._organizations:
            raise OrganizationNotFoundError()

        organization = self._organizations[id]

        if expiration_date is not Unset:
            organization = organization.evolve(expiration_date=expiration_date)
        if user_profile_outsider_allowed is not Unset:
            organization = organization.evolve(
                user_profile_outsider_allowed=user_profile_outsider_allowed
            )
        if active_users_limit is not Unset:
            organization = organization.evolve(active_users_limit=active_users_limit)

        self._organizations[id] = organization

        if self._organizations[id].is_expired:
            await self._send_event(BackendEvent.ORGANIZATION_EXPIRED, organization_id=id)
