# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from functools import wraps
from typing import List, Union

import trio

from parsec.api.protocol import (
    APIV1_HandshakeType,
    HandshakeType,
    InvalidMessageError,
    ProtocolError,
)
from parsec.api.version import API_V1_VERSION, API_V2_VERSION

ALLOWED_API_VERSIONS = {API_V1_VERSION.version, API_V2_VERSION.version}


def api(
    cmd: str,
    *,
    handshake_types: List[Union[HandshakeType, APIV1_HandshakeType]] = (
        HandshakeType.AUTHENTICATED,
        APIV1_HandshakeType.AUTHENTICATED,
    ),
):
    def wrapper(fn):
        assert not hasattr(fn, "_api_info")
        fn._api_info = {"cmd": cmd, "handshake_types": handshake_types}
        return fn

    return wrapper


def collect_apis(*components):
    apis = {}
    for component in components:
        for methname in dir(component):
            meth = getattr(component, methname)
            info = getattr(meth, "_api_info", None)
            if not info:
                continue

            for handshake_type in info["handshake_types"]:
                if handshake_type not in apis:
                    apis[handshake_type] = {}

                assert info["cmd"] not in apis[handshake_type]
                apis[handshake_type][info["cmd"]] = meth

    return apis


def check_anonymous_api_allowed(fn):
    if not getattr(fn, "_anonymous_api_allowed", False):
        raise RuntimeError(
            f"Trying to add {fn!r} to the anonymous api "
            "(need to use @anonymous_api decorator for this)."
        )


def catch_protocol_errors(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)

        except InvalidMessageError as exc:
            return {"status": "bad_message", "errors": exc.errors, "reason": "Invalid message."}

        except ProtocolError as exc:
            return {"status": "bad_message", "reason": str(exc)}

    return wrapper


class CancelledByNewRequest(Exception):
    def __init__(self, new_raw_req):
        self.new_raw_req = new_raw_req


async def run_with_breathing_transport(transport, fn, *args, **kwargs):
    """
    This is kind of a special case here:
    unlike other requests this one is going to (potentially) take
    a long time to complete. In the meantime we must monitor the
    connection with the client in order to make sure it is still
    online and handles websocket pings
    """

    rep = None

    async def _keep_transport_breathing():
        # If a command is received, the client is violating the
        # request/reply pattern. We consider this as an order to stop
        # listening events.
        raw_req = await transport.recv()
        raise CancelledByNewRequest(raw_req)

    async def _do_fn(cancel_scope):
        nonlocal rep
        rep = await fn(*args, **kwargs)
        cancel_scope.cancel()

    async with trio.open_service_nursery() as nursery:
        nursery.start_soon(_do_fn, nursery.cancel_scope)
        nursery.start_soon(_keep_transport_breathing)

    return rep
