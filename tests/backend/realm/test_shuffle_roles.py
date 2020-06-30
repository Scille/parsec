# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from unittest.mock import ANY

import pytest
import trio
from hypothesis import strategies as st
from hypothesis_trio.stateful import (
    Bundle,
    TrioAsyncioRuleBasedStateMachine,
    initialize,
    invariant,
    multiple,
    rule,
    run_state_machine_as_test,
)
from pendulum import now as pendulum_now

from parsec.api.data import RealmRoleCertificateContent
from parsec.api.protocol import RealmRole
from tests.backend.common import realm_get_role_certificates, realm_update_roles
from tests.common import call_with_control


@pytest.mark.slow
def test_shuffle_roles(
    hypothesis_settings,
    reset_testbed,
    backend_addr,
    backend_factory,
    server_factory,
    backend_data_binder_factory,
    backend_sock_factory,
    local_device_factory,
    realm_factory,
    coolorg,
    alice,
):
    class ShuffleRoles(TrioAsyncioRuleBasedStateMachine):
        realm_role_strategy = st.one_of(st.just(x) for x in RealmRole)
        User = Bundle("user")

        async def start_backend(self):
            async def _backend_controlled_cb(started_cb):
                async with backend_factory(populated=False) as backend:
                    async with server_factory(backend.handle_client, backend_addr) as server:
                        await started_cb(backend=backend, server=server)

            return await self.get_root_nursery().start(call_with_control, _backend_controlled_cb)

        @property
        def backend(self):
            return self.backend_controller.backend

        @initialize(target=User)
        async def init(self):
            await reset_testbed()
            self.backend_controller = await self.start_backend()

            # Create organization and first user
            self.backend_data_binder = backend_data_binder_factory(self.backend)
            await self.backend_data_binder.bind_organization(coolorg, alice)

            # Create realm
            self.realm_id = await realm_factory(self.backend, alice)
            self.current_roles = {alice.user_id: RealmRole.OWNER}
            self.certifs = [ANY]

            self.socks = {}
            return alice

        async def get_sock(self, device):
            try:
                return self.socks[device.user_id]
            except KeyError:
                pass

            async def _start_sock(device, *, task_status=trio.TASK_STATUS_IGNORED):
                async with backend_sock_factory(self.backend, device) as sock:
                    task_status.started(sock)
                    await trio.sleep_forever()

            sock = await self.get_root_nursery().start(_start_sock, device)
            self.socks[device.user_id] = sock
            return sock

        @rule(target=User, author=User, role=realm_role_strategy)
        async def give_role_to_new_user(self, author, role):
            # Create new user/device
            new_device = local_device_factory()
            await self.backend_data_binder.bind_device(new_device)
            self.current_roles[new_device.user_id] = None
            # Assign role
            author_sock = await self.get_sock(author)
            if await self._give_role(author_sock, author, new_device, role):
                return new_device
            else:
                return multiple()

        @rule(author=User, recipient=User, role=realm_role_strategy)
        async def change_role_for_existing_user(self, author, recipient, role):
            author_sock = await self.get_sock(author)
            await self._give_role(author_sock, author, recipient, role)

        async def _give_role(self, author_sock, author, recipient, role):
            author_sock = await self.get_sock(author)

            certif = RealmRoleCertificateContent(
                author=author.device_id,
                timestamp=pendulum_now(),
                realm_id=self.realm_id,
                user_id=recipient.user_id,
                role=role,
            ).dump_and_sign(author.signing_key)
            rep = await realm_update_roles(author_sock, certif, check_rep=False)
            if author.user_id == recipient.user_id:
                assert rep == {
                    "status": "invalid_data",
                    "reason": "Realm role certificate cannot be self-signed.",
                }

            else:
                owner_only = (RealmRole.OWNER,)
                owner_or_manager = (RealmRole.OWNER, RealmRole.MANAGER)
                existing_recipient_role = self.current_roles[recipient.user_id]
                if existing_recipient_role in owner_or_manager or role in owner_or_manager:
                    allowed_roles = owner_only
                else:
                    allowed_roles = owner_or_manager

                if self.current_roles[author.user_id] in allowed_roles:
                    # print(f"+ {author.user_id} -{role.value}-> {recipient.user_id}")
                    if existing_recipient_role != role:
                        assert rep == {"status": "ok"}
                        self.current_roles[recipient.user_id] = role
                        self.certifs.append(certif)
                    else:
                        assert rep == {"status": "already_granted"}
                else:
                    # print(f"- {author.user_id} -{role.value}-> {recipient.user_id}")
                    assert rep == {"status": "not_allowed"}

            return rep["status"] == "ok"

        @rule(author=User)
        async def get_role_certificates(self, author):
            sock = await self.get_sock(author)
            rep = await realm_get_role_certificates(sock, self.realm_id)
            if self.current_roles.get(author.user_id) is not None:
                assert rep["status"] == "ok"
                assert rep["certificates"] == self.certifs
            else:
                assert rep == {}

        @invariant()
        async def check_current_roles(self):
            try:
                backend = self.backend
            except AttributeError:
                return
            roles = await backend.realm.get_current_roles(alice.organization_id, self.realm_id)
            assert roles == {k: v for k, v in self.current_roles.items() if v is not None}

    run_state_machine_as_test(ShuffleRoles, settings=hypothesis_settings)
