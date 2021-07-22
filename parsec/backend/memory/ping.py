# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from parsec.api.protocol import DeviceID, OrganizationID
from parsec.backend.ping import BasePingComponent
from parsec.backend.backend_events import BackendEvent


class MemoryPingComponent(BasePingComponent):
    def __init__(self, send_event):
        self._send_event = send_event

    def register_components(self, **other_components):
        pass

    async def ping(self, organization_id: OrganizationID, author: DeviceID, ping: str) -> None:
        if author:
            await self._send_event(
                BackendEvent.PINGED, organization_id=organization_id, author=author, ping=ping
            )
