# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio

from parsec.api.protocol import APIV1_ADMINISTRATION_CMDS
from parsec.core.backend_connection import (
    BackendConnectionRefused,
    BackendNotAvailable,
    apiv1_backend_administration_cmds_factory,
)
from tests.core.backend_connection.common import ALL_CMDS


@pytest.mark.trio
async def test_backend_offline(backend_addr, backend):
    with pytest.raises(BackendNotAvailable):
        async with apiv1_backend_administration_cmds_factory(
            backend_addr, backend.config.administration_token
        ) as cmds:
            await cmds.ping()


@pytest.mark.trio
async def test_backend_switch_offline(running_backend):
    async with apiv1_backend_administration_cmds_factory(
        running_backend.addr, running_backend.backend.config.administration_token
    ) as cmds:
        await cmds.ping()

        with running_backend.offline():
            with pytest.raises(BackendNotAvailable):
                await cmds.ping()


@pytest.mark.trio
async def test_backend_closed_cmds(running_backend):
    async with apiv1_backend_administration_cmds_factory(
        running_backend.addr, running_backend.backend.config.administration_token
    ) as cmds:
        pass
    with pytest.raises(trio.ClosedResourceError):
        await cmds.ping()


@pytest.mark.trio
async def test_ping(running_backend):
    async with apiv1_backend_administration_cmds_factory(
        running_backend.addr, running_backend.backend.config.administration_token
    ) as cmds:
        rep = await cmds.ping("Hello World !")
        assert rep == {"status": "ok", "pong": "Hello World !"}


@pytest.mark.trio
async def test_handshake_invalid_token(running_backend):
    with pytest.raises(BackendConnectionRefused) as exc:
        async with apiv1_backend_administration_cmds_factory(running_backend.addr, "dummy") as cmds:
            await cmds.ping()
    assert str(exc.value) == "Invalid administration token"


@pytest.mark.trio
async def test_administration_cmds_has_right_methods(running_backend):
    async with apiv1_backend_administration_cmds_factory(
        running_backend.addr, running_backend.backend.config.administration_token
    ) as cmds:
        for method_name in APIV1_ADMINISTRATION_CMDS:
            assert hasattr(cmds, method_name)
        for method_name in ALL_CMDS - APIV1_ADMINISTRATION_CMDS:
            assert not hasattr(cmds, method_name)
