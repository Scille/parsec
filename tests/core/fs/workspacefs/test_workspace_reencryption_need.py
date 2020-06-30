# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from hypothesis_trio.stateful import (
    Bundle,
    TrioAsyncioRuleBasedStateMachine,
    consumes,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)
from pendulum import now as pendulum_now

from parsec.api.data import RealmRoleCertificateContent
from parsec.api.protocol import RealmRole
from parsec.backend.realm import RealmGrantedRole
from tests.common import call_with_control


@pytest.mark.slow
def test_workspace_reencryption_need(
    hypothesis_settings,
    reset_testbed,
    backend_addr,
    backend_factory,
    backend_data_binder_factory,
    server_factory,
    oracle_fs_with_sync_factory,
    user_fs_factory,
    local_device_factory,
    alice,
):
    class WorkspaceFSReencrytionNeed(TrioAsyncioRuleBasedStateMachine):
        Users = Bundle("user")

        async def start_user_fs(self):
            try:
                await self.user_fs_controller.stop()
            except AttributeError:
                pass

            async def _user_fs_controlled_cb(started_cb):
                async with user_fs_factory(device=alice) as user_fs:
                    await started_cb(user_fs=user_fs)

            self.user_fs_controller = await self.get_root_nursery().start(
                call_with_control, _user_fs_controlled_cb
            )

        async def start_backend(self):
            async def _backend_controlled_cb(started_cb):
                async with backend_factory() as backend:
                    async with server_factory(backend.handle_client, backend_addr) as server:
                        await started_cb(backend=backend, server=server)

            self.backend_controller = await self.get_root_nursery().start(
                call_with_control, _backend_controlled_cb
            )

        def _oracle_give_or_change_role(self, user):
            current_role = self.user_roles.get(user.user_id)
            new_role = RealmRole.MANAGER if current_role != RealmRole.MANAGER else RealmRole.READER
            self.since_reencryption_role_revoked.discard(user.user_id)
            self.user_roles[user.user_id] = new_role
            return new_role

        def _oracle_revoke_role(self, user):
            if user.user_id in self.user_roles:
                self.since_reencryption_role_revoked.add(user.user_id)
                self.user_roles.pop(user.user_id, None)
                return True
            else:
                return False

        def _oracle_revoke_user(self, user):
            if user.user_id in self.user_roles:
                self.since_reencryption_user_revoked.add(user.user_id)

        async def _update_role(self, author, user, role=RealmRole.MANAGER):
            now = pendulum_now()
            certif = RealmRoleCertificateContent(
                author=author.device_id,
                timestamp=now,
                realm_id=self.wid,
                user_id=user.user_id,
                role=role,
            ).dump_and_sign(author.signing_key)
            await self.backend.realm.update_roles(
                author.organization_id,
                RealmGrantedRole(
                    certificate=certif,
                    realm_id=self.wid,
                    user_id=user.user_id,
                    role=role,
                    granted_by=author.device_id,
                    granted_on=now,
                ),
            )
            return certif

        @property
        def user_fs(self):
            return self.user_fs_controller.user_fs

        @property
        def backend(self):
            return self.backend_controller.backend

        @initialize()
        async def init(self):
            await reset_testbed()

            self.user_roles = {}
            self.since_reencryption_user_revoked = set()
            self.since_reencryption_role_revoked = set()

            await self.start_backend()
            self.backend_data_binder = backend_data_binder_factory(self.backend)

            await self.start_user_fs()
            self.wid = await self.user_fs.workspace_create("w")
            await self.user_fs.sync()
            self.workspacefs = self.user_fs.get_workspace(self.wid)

        @rule(target=Users)
        async def give_role(self):
            new_user = local_device_factory()
            await self.backend_data_binder.bind_device(new_user)
            new_role = self._oracle_give_or_change_role(new_user)
            await self._update_role(alice, new_user, role=new_role)
            return new_user

        @rule(user=Users)
        async def change_role(self, user):
            new_role = self._oracle_give_or_change_role(user)
            await self._update_role(alice, user, role=new_role)

        @rule(user=Users)
        async def revoke_role(self, user):
            if self._oracle_revoke_role(user):
                await self._update_role(alice, user, role=None)

        @rule(user=consumes(Users))
        async def revoke_user(self, user):
            await self.backend_data_binder.bind_revocation(user.user_id, alice)
            self._oracle_revoke_user(user)

        @rule()
        async def reencrypt(self):
            job = await self.user_fs.workspace_start_reencryption(self.wid)
            while True:
                total, done = await job.do_one_batch()
                if total <= done:
                    break
            self.since_reencryption_user_revoked.clear()
            self.since_reencryption_role_revoked.clear()
            # Needed to keep encryption revision up to date
            await self.user_fs.process_last_messages()

        @invariant()
        async def check_reencryption_need(self):
            if not hasattr(self, "workspacefs"):
                return
            need = await self.workspacefs.get_reencryption_need()
            assert set(need.role_revoked) == self.since_reencryption_role_revoked
            assert (
                set(need.user_revoked)
                == self.since_reencryption_user_revoked - self.since_reencryption_role_revoked
            )

    run_state_machine_as_test(WorkspaceFSReencrytionNeed, settings=hypothesis_settings)
