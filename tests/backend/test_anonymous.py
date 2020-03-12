# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
from unittest.mock import patch

from parsec.api.protocol.base import packb, unpackb
from parsec.api.protocol import ADMINISTRATION_CMDS, AUTHENTICATED_CMDS, ANONYMOUS_CMDS

from parsec.backend.utils import anonymous_api, check_anonymous_api_allowed


def test_anonymous_api_decorator():
    @anonymous_api
    def anonymous_ok():
        pass

    def anonymous_ko():
        pass

    check_anonymous_api_allowed(anonymous_ok)
    with pytest.raises(RuntimeError):
        check_anonymous_api_allowed(anonymous_ko)


@pytest.mark.trio
async def test_anonymous_api_in_backend_app(backend_factory):
    def not_decorated():
        pass

    with patch("parsec.backend.user.BaseUserComponent.api_user_claim", new=not_decorated):
        with pytest.raises(RuntimeError):
            async with backend_factory():
                pass


@pytest.mark.trio
async def test_connect_as_anonymous(anonymous_backend_sock):
    await anonymous_backend_sock.send(packb({"cmd": "ping", "ping": "foo"}))
    rep = await anonymous_backend_sock.recv()
    assert unpackb(rep) == {"status": "ok", "pong": "foo"}


@pytest.mark.trio
async def test_anonymous_has_limited_access(anonymous_backend_sock):
    for cmd in (ADMINISTRATION_CMDS | AUTHENTICATED_CMDS) - ANONYMOUS_CMDS:
        await anonymous_backend_sock.send(packb({"cmd": cmd}))
        rep = await anonymous_backend_sock.recv()
        assert unpackb(rep) == {"status": "unknown_command", "reason": "Unknown command"}
