# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import trio

from parsec.utils import start_task


async def job(fail=0, task_status=trio.TASK_STATUS_IGNORED):
    await trio.sleep(1)
    if fail < 0:
        raise RuntimeError("Oops")
    success = set()
    task_status.started(success)
    await trio.sleep(1)
    if fail > 0:
        raise RuntimeError("Oops")
    success.add(1)


@pytest.mark.trio
async def test_start_task_and_join(autojump_clock):
    async with trio.open_service_nursery() as nursery:
        status = await start_task(nursery, job)
        success = status.value
        assert success == set()
        assert not status.finished
        await status.join()
        assert status.finished
        assert success


@pytest.mark.trio
async def test_start_task_cancel_and_join(autojump_clock):
    async with trio.open_service_nursery() as nursery:
        status = await start_task(nursery, job)
        success = status.value
        assert success == set()
        assert not status.finished
        await status.cancel_and_join()
        assert status.cancel_called
        assert status.finished
        assert not status.value


@pytest.mark.trio
async def test_start_task_with_exception_before_started(autojump_clock):
    async with trio.open_service_nursery() as nursery:
        with pytest.raises(RuntimeError):
            await start_task(nursery, job, -1)


@pytest.mark.trio
async def test_start_task_with_exception_after_started(autojump_clock):
    with pytest.raises(RuntimeError):
        async with trio.open_service_nursery() as nursery:
            status = await start_task(nursery, job, +1)
            success = status.value
            assert success == set()
            assert not status.finished
            await status.join()
            assert False  # pragma: no cover - gets cancelled
