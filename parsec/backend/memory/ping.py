# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.protocol import DeviceID, OrganizationID
from parsec.backend.backend_events import BackendEvent
from parsec.backend.ping import BasePingComponent


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
