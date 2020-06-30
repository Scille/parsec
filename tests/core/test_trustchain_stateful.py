# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from hypothesis import note
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    initialize,
    precondition,
    rule,
    run_state_machine_as_test,
)
from pendulum import now as pendulum_now

from parsec.api.data import (
    DeviceCertificateContent,
    RevokedUserCertificateContent,
    UserCertificateContent,
    UserProfile,
)
from parsec.api.protocol import UserID
from parsec.core.trustchain import TrustchainContext


@pytest.mark.slow
def test_workspace_reencryption_need(hypothesis_settings, caplog, local_device_factory, coolorg):
    name_count = 0

    class TrustchainValidate(RuleBasedStateMachine):
        NonRevokedAdminUsers = Bundle("admin users")
        NonRevokedOtherUsers = Bundle("other users")
        RevokedUsers = Bundle("revoked users")

        def next_user_id(self):
            nonlocal name_count
            name_count += 1
            return UserID(f"user{name_count}")

        def next_device_id(self, user_id=None):
            nonlocal name_count
            user_id = user_id or self.next_user_id()
            name_count += 1
            return user_id.to_device_id(f"dev{name_count}")

        def new_user_and_device(self, is_admin, certifier_id, certifier_key):
            device_id = self.next_device_id()

            local_device = local_device_factory(device_id, org=coolorg)
            self.local_devices[device_id] = local_device

            user = UserCertificateContent(
                author=certifier_id,
                timestamp=pendulum_now(),
                user_id=local_device.user_id,
                human_handle=local_device.human_handle,
                public_key=local_device.public_key,
                profile=UserProfile.ADMIN if is_admin else UserProfile.STANDARD,
            )
            self.users_content[device_id.user_id] = user
            self.users_certifs[device_id.user_id] = user.dump_and_sign(certifier_key)

            device = DeviceCertificateContent(
                author=certifier_id,
                timestamp=pendulum_now(),
                device_id=local_device.device_id,
                device_label=local_device.device_label,
                verify_key=local_device.verify_key,
            )
            self.devices_content[local_device.device_id] = device
            self.devices_certifs[local_device.device_id] = device.dump_and_sign(certifier_key)

            return device_id

        @initialize(target=NonRevokedAdminUsers)
        def init(self):
            caplog.clear()
            self.users_certifs = {}
            self.users_content = {}
            self.revoked_users_certifs = {}
            self.revoked_users_content = {}
            self.devices_certifs = {}
            self.devices_content = {}
            self.local_devices = {}
            device_id = self.new_user_and_device(
                is_admin=True, certifier_id=None, certifier_key=coolorg.root_signing_key
            )
            note(f"new device: {device_id}")
            return device_id.user_id

        def get_device(self, user_id, device_rand):
            user_devices = [
                device
                for device_id, device in self.local_devices.items()
                if device_id.user_id == user_id
            ]
            return user_devices[device_rand % len(user_devices)]

        @rule(
            target=NonRevokedAdminUsers,
            author_user=NonRevokedAdminUsers,
            author_device_rand=st.integers(min_value=0),
        )
        def new_admin_user(self, author_user, author_device_rand):
            author = self.get_device(author_user, author_device_rand)
            device_id = self.new_user_and_device(
                is_admin=True, certifier_id=author.device_id, certifier_key=author.signing_key
            )
            note(f"new device: {device_id} (author: {author.device_id})")
            return device_id.user_id

        @rule(
            target=NonRevokedOtherUsers,
            author_user=NonRevokedAdminUsers,
            author_device_rand=st.integers(min_value=0),
        )
        def new_non_admin_user(self, author_user, author_device_rand):
            author = self.get_device(author_user, author_device_rand)
            device_id = self.new_user_and_device(
                is_admin=False, certifier_id=author.device_id, certifier_key=author.signing_key
            )
            note(f"new device: {device_id} (author: {author.device_id})")
            return device_id.user_id

        @precondition(lambda self: len([d for d in self.local_devices.values() if d.is_admin]) > 1)
        @rule(
            target=RevokedUsers,
            user=st.one_of(consumes(NonRevokedAdminUsers), consumes(NonRevokedOtherUsers)),
            author_rand=st.integers(min_value=0),
        )
        def revoke_user(self, user, author_rand):
            possible_authors = [
                device
                for device_id, device in self.local_devices.items()
                if device_id.user_id != user and device.profile == UserProfile.ADMIN
            ]
            author = possible_authors[author_rand % len(possible_authors)]
            note(f"revoke user: {user} (author: {author.device_id})")
            revoked_user = RevokedUserCertificateContent(
                author=author.device_id, timestamp=pendulum_now(), user_id=user
            )
            self.revoked_users_content[user] = revoked_user
            self.revoked_users_certifs[user] = revoked_user.dump_and_sign(author.signing_key)
            return user

        @rule(
            user=st.one_of(NonRevokedAdminUsers, NonRevokedOtherUsers),
            author_user=NonRevokedAdminUsers,
            author_device_rand=st.integers(min_value=0),
        )
        def new_device(self, user, author_user, author_device_rand):
            author = self.get_device(author_user, author_device_rand)
            device_id = self.next_device_id(user)
            note(f"new device: {device_id} (author: {author.device_id})")
            local_device = local_device_factory(device_id, org=coolorg)
            device = DeviceCertificateContent(
                author=author.device_id,
                timestamp=pendulum_now(),
                device_id=local_device.device_id,
                device_label=local_device.device_label,
                verify_key=local_device.verify_key,
            )
            self.devices_content[local_device.device_id] = device
            self.devices_certifs[local_device.device_id] = device.dump_and_sign(author.signing_key)

        @rule(user=st.one_of(NonRevokedAdminUsers, NonRevokedOtherUsers))
        def load_trustchain(self, user):
            ctx = TrustchainContext(coolorg.root_verify_key, 1)

            user_certif = next(
                certif for user_id, certif in self.users_certifs.items() if user_id == user
            )
            revoked_user_certif = next(
                (
                    certif
                    for user_id, certif in self.revoked_users_certifs.items()
                    if user_id == user
                ),
                None,
            )
            devices_certifs = [
                certif
                for device_id, certif in self.devices_certifs.items()
                if device_id.user_id == user
            ]
            user_content, revoked_user_content, devices_contents = ctx.load_user_and_devices(
                trustchain={
                    "users": self.users_certifs.values(),
                    "revoked_users": self.revoked_users_certifs.values(),
                    "devices": self.devices_certifs.values(),
                },
                user_certif=user_certif,
                revoked_user_certif=revoked_user_certif,
                devices_certifs=devices_certifs,
                expected_user_id=user,
            )

            expected_user_content = next(
                content for user_id, content in self.users_content.items() if user_id == user
            )
            expected_revoked_user_content = next(
                (
                    content
                    for user_id, content in self.revoked_users_content.items()
                    if user_id == user
                ),
                None,
            )
            expected_devices_contents = [
                content
                for device_id, content in self.devices_content.items()
                if device_id.user_id == user
            ]
            assert user_content == expected_user_content
            assert revoked_user_content == expected_revoked_user_content
            assert devices_contents == expected_devices_contents

    run_state_machine_as_test(TrustchainValidate, settings=hypothesis_settings)
