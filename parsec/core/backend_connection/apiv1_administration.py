# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import AsyncGenerator, Optional

import trio
from async_generator import asynccontextmanager

from parsec.api.protocol import APIV1_ADMINISTRATION_CMDS
from parsec.core.backend_connection.exceptions import BackendNotAvailable
from parsec.core.backend_connection.expose_cmds import expose_cmds
from parsec.core.backend_connection.transport import apiv1_connect
from parsec.core.types import BackendOrganizationAddr


class APIV1_BackendAdministrationCmds:
    def __init__(self, addr, acquire_transport):
        self.addr = addr
        self.acquire_transport = acquire_transport

    for cmd_name in APIV1_ADMINISTRATION_CMDS:
        vars()[cmd_name] = expose_cmds(cmd_name, apiv1=True)


@asynccontextmanager
async def apiv1_backend_administration_cmds_factory(
    addr: BackendOrganizationAddr, token: str, keepalive: Optional[int] = None
) -> AsyncGenerator[APIV1_BackendAdministrationCmds, None]:
    """
    Raises:
        BackendConnectionError
    """
    transport_lock = trio.Lock()
    transport = None
    closed = False

    async def _init_transport():
        nonlocal transport
        if not transport:
            if closed:
                raise trio.ClosedResourceError
            transport = await apiv1_connect(addr, administration_token=token, keepalive=keepalive)
            transport.logger = transport.logger.bind(auth="<administration>")

    async def _destroy_transport():
        nonlocal transport
        if transport:
            await transport.aclose()
            transport = None

    @asynccontextmanager
    async def _acquire_transport(**kwargs):
        nonlocal transport

        async with transport_lock:
            await _init_transport()
            try:
                yield transport
            except BackendNotAvailable:
                await _destroy_transport()
                raise

    try:
        yield APIV1_BackendAdministrationCmds(addr, _acquire_transport)

    finally:
        async with transport_lock:
            closed = True
            await _destroy_transport()
