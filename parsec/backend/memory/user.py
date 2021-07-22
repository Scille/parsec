# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

from parsec.backend.backend_events import BackendEvent
import attr
import pendulum
from typing import Tuple, List, Dict, Optional
from collections import defaultdict

from parsec.api.protocol import OrganizationID, UserID, DeviceID, DeviceName, HumanHandle
from parsec.backend.user import (
    BaseUserComponent,
    User,
    Device,
    Trustchain,
    GetUserAndDevicesResult,
    HumanFindResultItem,
    UserInvitation,
    DeviceInvitation,
    UserAlreadyExistsError,
    UserAlreadyRevokedError,
    UserNotFoundError,
    UserLimitReached,
)


@attr.s
class OrganizationStore:
    human_handle_to_user_id: Dict[HumanHandle, UserID] = attr.ib(factory=dict)
    users: Dict[UserID, User] = attr.ib(factory=dict)
    devices: Dict[UserID, Dict[DeviceName, Device]] = attr.ib(factory=lambda: defaultdict(dict))
    invitations: Dict[UserID, UserInvitation] = attr.ib(factory=dict)


class MemoryUserComponent(BaseUserComponent):
    def __init__(self, send_event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._send_event = send_event
        self._organizations = defaultdict(OrganizationStore)

    def register_components(self, **other_components):
        self._organization_component = other_components["organization"]

    async def create_user(
        self, organization_id: OrganizationID, user: User, first_device: Device
    ) -> None:
        org = self._organizations[organization_id]
        active_users_limit = self._organization_component._organizations[
            organization_id
        ].active_users_limit

        if active_users_limit and active_users_limit <= len(org.users):
            raise UserLimitReached()

        if user.user_id in org.users:
            raise UserAlreadyExistsError(f"User `{user.user_id}` already exists")

        if user.human_handle and user.human_handle in org.human_handle_to_user_id:
            raise UserAlreadyExistsError(
                f"Human handle `{user.human_handle}` already corresponds to a non-revoked user"
            )

        org.users[user.user_id] = user
        org.devices[first_device.user_id][first_device.device_name] = first_device
        if user.human_handle:
            org.human_handle_to_user_id[user.human_handle] = user.user_id

        await self._send_event(
            BackendEvent.USER_CREATED,
            organization_id=organization_id,
            user_id=user.user_id,
            user_certificate=user.user_certificate,
            first_device_id=first_device.device_id,
            first_device_certificate=first_device.device_certificate,
        )

    async def create_device(
        self, organization_id: OrganizationID, device: Device, encrypted_answer: bytes = b""
    ) -> None:
        org = self._organizations[organization_id]

        if device.user_id not in org.users:
            raise UserNotFoundError(f"User `{device.user_id}` doesn't exists")

        user_devices = org.devices[device.user_id]
        if device.device_name in user_devices:
            raise UserAlreadyExistsError(f"Device `{device.device_id}` already exists")

        user_devices[device.device_name] = device
        await self._send_event(
            BackendEvent.DEVICE_CREATED,
            organization_id=organization_id,
            device_id=device.device_id,
            device_certificate=device.device_certificate,
            encrypted_answer=encrypted_answer,
        )

    async def _get_trustchain(
        self, organization_id: OrganizationID, *devices_ids, redacted: bool = False
    ):
        trustchain_devices = set()
        trustchain_users = set()
        trustchain_revoked_users = set()
        in_trustchain = set()

        user_certif_field = "redacted_user_certificate" if redacted else "user_certificate"
        device_certif_field = "redacted_device_certificate" if redacted else "device_certificate"

        async def _recursive_extract_creators(device_id):
            if not device_id or device_id in in_trustchain:
                return
            in_trustchain.add(device_id)
            user = self._get_user(organization_id, device_id.user_id)
            device = self._get_device(organization_id, device_id)
            trustchain_devices.add(getattr(device, device_certif_field))
            trustchain_users.add(getattr(user, user_certif_field))
            if user.revoked_user_certificate:
                trustchain_revoked_users.add(user.revoked_user_certificate)
            await _recursive_extract_creators(device.device_certifier)
            await _recursive_extract_creators(user.revoked_user_certifier)
            await _recursive_extract_creators(user.user_certifier)

        for device_id in devices_ids:
            await _recursive_extract_creators(device_id)

        return Trustchain(
            devices=tuple(trustchain_devices),
            users=tuple(trustchain_users),
            revoked_users=tuple(trustchain_revoked_users),
        )

    def _get_user(self, organization_id: OrganizationID, user_id: UserID) -> User:
        org = self._organizations[organization_id]

        try:
            return org.users[user_id]

        except KeyError:
            raise UserNotFoundError(user_id)

    async def get_user(self, organization_id: OrganizationID, user_id: UserID) -> User:
        return self._get_user(organization_id, user_id)

    async def get_user_with_trustchain(
        self, organization_id: OrganizationID, user_id: UserID
    ) -> Tuple[User, Trustchain]:
        user = self._get_user(organization_id, user_id)
        trustchain = await self._get_trustchain(
            organization_id, user.user_certifier, user.revoked_user_certifier
        )
        return user, trustchain

    async def get_user_with_device_and_trustchain(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> Tuple[User, Device, Trustchain]:
        user = self._get_user(organization_id, device_id.user_id)
        user_device = self._get_device(organization_id, device_id)
        trustchain = await self._get_trustchain(
            organization_id,
            user.user_certifier,
            user.revoked_user_certifier,
            user_device.device_certifier,
        )
        return user, user_device, trustchain

    async def get_user_with_devices_and_trustchain(
        self, organization_id: OrganizationID, user_id: UserID, redacted: bool = False
    ) -> GetUserAndDevicesResult:
        user = self._get_user(organization_id, user_id)
        user_devices = self._get_user_devices(organization_id, user_id)
        user_devices_values = tuple(user_devices.values())
        trustchain = await self._get_trustchain(
            organization_id,
            user.user_certifier,
            user.revoked_user_certifier,
            *[device.device_certifier for device in user_devices_values],
            redacted=redacted,
        )
        return GetUserAndDevicesResult(
            user_certificate=user.redacted_user_certificate if redacted else user.user_certificate,
            revoked_user_certificate=user.revoked_user_certificate,
            device_certificates=tuple(
                d.redacted_device_certificate if redacted else d.device_certificate
                for d in user_devices.values()
            ),
            trustchain_device_certificates=trustchain.devices,
            trustchain_user_certificates=trustchain.users,
            trustchain_revoked_user_certificates=trustchain.revoked_users,
        )

    def _get_device(self, organization_id: OrganizationID, device_id: DeviceID) -> Device:
        org = self._organizations[organization_id]

        try:
            return org.devices[device_id.user_id][device_id.device_name]

        except KeyError:
            raise UserNotFoundError(device_id)

    def _get_user_devices(
        self, organization_id: OrganizationID, user_id: UserID
    ) -> Dict[DeviceName, Device]:
        org = self._organizations[organization_id]
        # Make sure user exists
        self._get_user(organization_id, user_id)
        return org.devices[user_id]

    async def get_user_with_device(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> Tuple[User, Device]:
        user = self._get_user(organization_id, device_id.user_id)
        device = self._get_device(organization_id, device_id)
        return user, device

    async def find(
        self,
        organization_id: OrganizationID,
        query: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
        omit_revoked: bool = False,
    ):
        assert page >= 1
        assert per_page >= 1

        org = self._organizations[organization_id]
        users = org.users

        if query:
            try:
                UserID(query)
            except ValueError:
                # Contains invalid caracters, no need to go further
                return ([], 0)

            results = [
                user_id for user_id in users.keys() if user_id.lower().find(query.lower()) != -1
            ]

        else:
            results = users.keys()

        if omit_revoked:
            now = pendulum.now()

            def _user_is_revoked(user_id):
                revoked_on = org.users[user_id].revoked_on
                return revoked_on is not None and revoked_on <= now

            results = [user_id for user_id in results if not _user_is_revoked(user_id)]

        total = len(results)

        # PostgreSQL does case insensitive sort
        sorted_results = sorted(results, key=lambda s: s.lower())

        # Handle pagination
        paginated_results = sorted_results[(page - 1) * per_page : page * per_page]

        return paginated_results, total

    async def find_humans(
        self,
        organization_id: OrganizationID,
        query: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
        omit_revoked: bool = False,
        omit_non_human: bool = False,
    ) -> Tuple[List[HumanFindResultItem], int]:
        assert page >= 1
        assert per_page >= 1

        org = self._organizations[organization_id]

        # Query is run against human handle field, hence non-human are automatically ignored
        if query:
            users = []
            for user in org.users.values():
                # Handle a case insensitive find search to be conform with postgresql query
                if user.human_handle and (
                    str(user.human_handle.email).lower().find(query.lower()) != -1
                    or str(user.human_handle.label).lower().find(query.lower()) != -1
                ):
                    users.append(user)
        else:
            users = org.users.values()
            if omit_non_human:
                users = [r for r in users if r.human_handle]
        # Sort human by label
        users = [
            *sorted(
                [res for res in users if res.human_handle],
                key=lambda r: r.human_handle.label.lower(),
            ),
            *[res for res in users if not res.human_handle],
        ]
        now = pendulum.now()
        results = [
            HumanFindResultItem(
                user_id=user.user_id,
                human_handle=user.human_handle,
                revoked=(user.revoked_on is not None and user.revoked_on <= now),
            )
            for user in users
        ]

        if omit_revoked:
            results = [res for res in results if not res.revoked]

        total = len(results)

        # Handle pagination
        paginated_results = results[(page - 1) * per_page : page * per_page]

        return paginated_results, total

    async def create_user_invitation(
        self, organization_id: OrganizationID, invitation: UserInvitation
    ) -> None:
        org = self._organizations[organization_id]

        if invitation.user_id in org.users:
            raise UserAlreadyExistsError(f"User `{invitation.user_id}` already exists")
        org.invitations[invitation.user_id] = invitation

    async def get_user_invitation(
        self, organization_id: OrganizationID, user_id: UserID
    ) -> UserInvitation:
        org = self._organizations[organization_id]

        if user_id in org.users:
            raise UserAlreadyExistsError(user_id)
        try:
            return org.invitations[user_id]
        except KeyError:
            raise UserNotFoundError(user_id)

    async def claim_user_invitation(
        self, organization_id: OrganizationID, user_id: UserID, encrypted_claim: bytes = b""
    ) -> UserInvitation:
        invitation = await self.get_user_invitation(organization_id, user_id)
        await self._send_event(
            BackendEvent.USER_CLAIMED,
            organization_id=organization_id,
            user_id=invitation.user_id,
            encrypted_claim=encrypted_claim,
        )
        return invitation

    async def cancel_user_invitation(
        self, organization_id: OrganizationID, user_id: UserID
    ) -> None:
        org = self._organizations[organization_id]

        if org.invitations.pop(user_id, None):
            await self._send_event(
                BackendEvent.USER_INVITATION_CANCELLED,
                organization_id=organization_id,
                user_id=user_id,
            )

    async def create_device_invitation(
        self, organization_id: OrganizationID, invitation: DeviceInvitation
    ) -> None:
        org = self._organizations[organization_id]

        user_devices = self._get_user_devices(organization_id, invitation.device_id.user_id)
        if invitation.device_id.device_name in user_devices:
            raise UserAlreadyExistsError(f"Device `{invitation.device_id}` already exists")

        org.invitations[invitation.device_id] = invitation

    async def get_device_invitation(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> DeviceInvitation:
        org = self._organizations[organization_id]

        user_devices = self._get_user_devices(organization_id, device_id.user_id)
        if device_id.device_name in user_devices:
            raise UserAlreadyExistsError(device_id)

        try:
            return org.invitations[device_id]

        except KeyError:
            raise UserNotFoundError(device_id)

    async def claim_device_invitation(
        self, organization_id: OrganizationID, device_id: DeviceID, encrypted_claim: bytes = b""
    ) -> DeviceInvitation:
        invitation = await self.get_device_invitation(organization_id, device_id)
        await self._send_event(
            BackendEvent.DEVICE_CLAIMED,
            organization_id=organization_id,
            device_id=invitation.device_id,
            encrypted_claim=encrypted_claim,
        )
        return invitation

    async def cancel_device_invitation(
        self, organization_id: OrganizationID, device_id: DeviceID
    ) -> None:
        org = self._organizations[organization_id]

        if org.invitations.pop(device_id, None):
            await self._send_event(
                BackendEvent.DEVICE_INVITATION_CANCELLED,
                organization_id=organization_id,
                device_id=device_id,
            )

    async def revoke_user(
        self,
        organization_id: OrganizationID,
        user_id: UserID,
        revoked_user_certificate: bytes,
        revoked_user_certifier: DeviceID,
        revoked_on: Optional[pendulum.DateTime] = None,
    ) -> None:
        org = self._organizations[organization_id]

        try:
            user = org.users[user_id]

        except KeyError:
            raise UserNotFoundError(user_id)

        if user.revoked_on:
            raise UserAlreadyRevokedError()

        org.users[user_id] = user.evolve(
            revoked_on=revoked_on or pendulum.now(),
            revoked_user_certificate=revoked_user_certificate,
            revoked_user_certifier=revoked_user_certifier,
        )
        if user.human_handle:
            del org.human_handle_to_user_id[user.human_handle]

        await self._send_event(
            BackendEvent.USER_REVOKED, organization_id=organization_id, user_id=user_id
        )
