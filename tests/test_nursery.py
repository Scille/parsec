# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio

import parsec

# Importing parsec is enough to add open_service_nursery to trio
parsec


@pytest.mark.trio
async def test_open_service_nursery_exists():
    async with trio.open_service_nursery():
        pass


@pytest.mark.trio
async def test_open_service_nursery_multierror_collapse():
    async def _raise(exc):
        raise exc

    with pytest.raises(ZeroDivisionError) as ctx:
        async with trio.open_service_nursery():
            await _raise(ZeroDivisionError(1, 2, 3))

    exception = ctx.value
    assert isinstance(exception, ZeroDivisionError)
    assert exception.args == (1, 2, 3)

    assert not isinstance(exception, trio.MultiError)

    with pytest.raises(ZeroDivisionError) as ctx:
        async with trio.open_service_nursery() as nursery:
            nursery.start_soon(_raise, RuntimeError())
            await _raise(ZeroDivisionError(1, 2, 3))

    exception = ctx.value
    assert isinstance(exception, ZeroDivisionError)
    assert exception.args == (1, 2, 3)

    assert isinstance(exception, trio.MultiError)
    assert len(exception.exceptions) == 2

    a, b = exception.exceptions
    assert isinstance(a, ZeroDivisionError)
    assert not isinstance(a, trio.MultiError)
    assert isinstance(b, RuntimeError)
    assert not isinstance(b, trio.MultiError)


@pytest.mark.trio
async def test_open_service_nursery_multierror_with_cancelled():
    async def _raise(exc):
        raise exc

    with trio.CancelScope() as cancel_scope:
        async with trio.open_service_nursery() as nursery:
            nursery.start_soon(_raise, ZeroDivisionError(1, 2, 3))
            cancel_scope.cancel()
            await trio.sleep(1)

    with trio.CancelScope() as cancel_scope:
        async with trio.open_service_nursery() as nursery:
            nursery.start_soon(_raise, RuntimeError())
            cancel_scope.cancel()
            raise ZeroDivisionError(1, 2, 3)
