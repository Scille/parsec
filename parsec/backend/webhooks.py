# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import trio
from typing import Optional
from structlog import get_logger
from urllib.error import URLError
from urllib.request import urlopen, Request

from parsec.api.protocol import OrganizationID, DeviceID, organization_bootstrap_webhook_serializer


logger = get_logger()


def _do_urllib_request(url: str, data: bytes) -> None:
    req = Request(
        url, method="POST", headers={"content-type": "application/json; charset=utf-8"}, data=data
    )
    try:
        with urlopen(req, timeout=30) as rep:
            if not 200 <= rep.status < 300:
                logger.warning(
                    "webhook bad return status", url=url, data=data, return_status=rep.status
                )

    except URLError as exc:
        logger.warning("webhook failure", url=url, data=data, exc_info=exc)


class WebhooksComponent:
    def __init__(self, config):
        self._config = config

    async def on_organization_bootstrap(
        self,
        organization_id: OrganizationID,
        device_id: DeviceID,
        device_label: Optional[str],
        human_email: Optional[str],
        human_label: Optional[str],
    ):
        if not self._config.organization_bootstrap_webhook_url:
            return
        data = organization_bootstrap_webhook_serializer.dumps(
            {
                "organization_id": organization_id,
                "device_id": device_id,
                "device_label": device_label,
                "human_email": human_email,
                "human_label": human_label,
            }
        )
        await trio.to_thread.run_sync(
            _do_urllib_request, self._config.organization_bootstrap_webhook_url, data
        )
