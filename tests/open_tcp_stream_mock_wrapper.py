# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import inspect
from contextlib import contextmanager
from unittest.mock import patch

import trio

from parsec.core.types import BackendAddr


def addr_to_netloc(addr):
    assert isinstance(addr, BackendAddr)
    return f"{addr.hostname}:{addr.port}"


class OpenTCPStreamMockWrapper:
    def __init__(self):
        self._socks = []
        self._hooks = {}
        self._offlines = set()

    @contextmanager
    def install_hook(self, addr, hook):
        self.push_hook(addr, hook)
        try:
            yield
        finally:
            self.pop_hook(addr)

    def push_hook(self, addr, hook):
        netloc_suffix = addr_to_netloc(addr)
        assert netloc_suffix not in self._hooks
        self._hooks[netloc_suffix] = hook

    def pop_hook(self, addr):
        netloc_suffix = addr_to_netloc(addr)
        self._hooks.pop(netloc_suffix)

    def get_socks(self, addr):
        netloc_suffix = addr_to_netloc(addr)
        return [sock for netloc, sock in self._socks if netloc.endswith(netloc_suffix)]

    async def __call__(self, host, port, *args, **kwargs):
        netloc = f"{host}:{port}" if port is not None else host
        hook = next(
            (hook for netloc_suffix, hook in self._hooks.items() if netloc.endswith(netloc_suffix)),
            None,
        )
        if hook and not any(netloc.endswith(offline) for offline in self._offlines):
            if inspect.iscoroutinefunction(hook):
                sock = await hook(host, port, *args, **kwargs)
            else:
                sock = hook(host, port, *args, **kwargs)

            if sock:
                self._socks.append((netloc, sock))
                return sock

        raise ConnectionRefusedError(111, "Connection refused")

    def switch_offline(self, addr):
        netloc_suffix = addr_to_netloc(addr)
        if netloc_suffix in self._offlines:
            return

        for netloc, sock in self._socks:
            if not netloc.endswith(netloc_suffix):
                continue

            async def _broken_stream(*args, **kwargs):
                raise trio.BrokenResourceError()

            _broken_stream.old_send_all_hook = sock.send_stream.send_all_hook
            _broken_stream.old_receive_some_hook = sock.receive_stream.receive_some_hook

            sock.send_stream.send_all_hook = _broken_stream
            sock.receive_stream.receive_some_hook = _broken_stream

            # Unlike for send stream, patching `receive_some_hook` is not enough
            # because it is called by the stream before actually waiting for data
            # (so in case of a long receive call, we could be past the hook call
            # when patching, hence not blocking the next incoming frame)
            sock.receive_stream.put_eof()

        self._offlines.add(netloc_suffix)

    def switch_online(self, addr):
        netloc_suffix = addr_to_netloc(addr)
        if netloc_suffix not in self._offlines:
            return

        # Note we don't try to revert the patched streams because there
        # represent old connections that have been lost
        self._offlines.remove(netloc_suffix)

    @contextmanager
    def offline(self, addr):
        self.switch_offline(addr)
        try:
            yield

        finally:
            self.switch_online(addr)


@contextmanager
def wrap_open_tcp_stream():
    open_tcp_stream_mock_wrapper = OpenTCPStreamMockWrapper()
    with patch("trio.open_tcp_stream", new=open_tcp_stream_mock_wrapper):
        yield


@contextmanager
def offline(addr):
    if not isinstance(trio.open_tcp_stream, OpenTCPStreamMockWrapper):
        raise RuntimeError("`tcp_stream_spy` fixture is missing")
    trio.open_tcp_stream.switch_offline(addr)
    try:
        yield

    finally:
        trio.open_tcp_stream.switch_online(addr)
