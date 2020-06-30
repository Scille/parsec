# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import trio

from parsec.core.ipcinterface import (
    IPCCommand,
    IPCServerAlreadyRunning,
    IPCServerBadResponse,
    IPCServerError,
    _install_posix_file_lock,
    _install_win32_mutex,
    run_ipc_server,
    send_to_ipc_server,
)


@pytest.mark.trio
@pytest.mark.skipif(os.name != "nt", reason="Windows only")
async def test_win32_mutex():
    mut1 = uuid4().hex
    mut2 = uuid4().hex

    # Test multiple time to make sure we can re-acquire the mutex once released
    for _ in range(2):
        with _install_win32_mutex(mut1):
            # Check mutex works
            with pytest.raises(IPCServerAlreadyRunning):
                with _install_win32_mutex(mut1):
                    pass

            # Check we can create new mutex
            with _install_win32_mutex(mut2):
                pass


# @pytest.mark.trio
# @pytest.mark.skipif(os.name != "nt", reason="Windows only")
# async def test_win32_mutex_interprocess():
#     mutex_name = uuid4().hex
#     python_cmd = f"""
# from parsec.core.ipcinterface import _install_win32_mutex, IPCServerAlreadyRunning
# try:
#     with _install_win32_mutex("{mutex_name}"):
#         pass
# except IPCServerAlreadyRunning:
#     raise SystemExit(0)
# else:
#     raise SystemExit(1)
# """

#     with _install_win32_mutex(mutex_name):
#         ret = subprocess.run(
#             ["python", "-c", python_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE
#         )
#         print(ret.stdout.decode())
#         print(ret.stderr.decode())
#         assert ret.returncode == 0


@pytest.mark.trio
@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
async def test_posix_file_lock(tmpdir):
    file1 = Path(tmpdir / "1.lock")
    # Also check having missing folders in the path is not an issue
    file2 = Path(tmpdir / "missing_parent/missing_child/2.lock")

    # Test multiple time to make sure we can re-acquire the mutex once released
    for _ in range(2):
        with _install_posix_file_lock(file1):
            # Check mutex works
            with pytest.raises(IPCServerAlreadyRunning):
                with _install_posix_file_lock(file1):
                    pass

            # Check we can create new mutex
            with _install_posix_file_lock(file2):
                pass


@pytest.mark.trio
@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
async def test_posix_file_lock_interprocess(tmpdir):
    file1 = Path(tmpdir / "1.lock")
    python_cmd = f"""
from pathlib import Path
from parsec.core.ipcinterface import _install_posix_file_lock, IPCServerAlreadyRunning
try:
    with _install_posix_file_lock(Path("{file1}")):
        pass
except IPCServerAlreadyRunning:
    raise SystemExit(0)
else:
    raise SystemExit(1)
"""

    with _install_posix_file_lock(file1):
        ret = subprocess.run(
            ["python", "-c", python_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        print(ret.stdout.decode())
        print(ret.stderr.decode())
        assert ret.returncode == 0


@pytest.mark.trio
async def test_ipc_server(tmpdir, monkeypatch):
    # The dummy directory should be automatically created when the server starts
    file1 = Path(tmpdir / "dummy" / "1.lock")
    mut1 = uuid4().hex

    async def _cmd_handler(cmd):
        assert cmd == {"cmd": IPCCommand.FOREGROUND}
        return {"status": "ok"}

    mut1 = uuid4().hex
    with trio.fail_after(1):
        async with run_ipc_server(_cmd_handler, socket_file=file1, win32_mutex_name=mut1):

            with pytest.raises(IPCServerAlreadyRunning):
                async with run_ipc_server(_cmd_handler, socket_file=file1, win32_mutex_name=mut1):
                    pass

            # Send good command
            ret = await send_to_ipc_server(file1, IPCCommand.FOREGROUND)
            assert ret == {"status": "ok"}

            # Send bad command, should be catched before even trying to reach the server
            with pytest.raises(IPCServerError) as exc:
                ret = await send_to_ipc_server(file1, "dummy")
            assert str(exc.value).startswith("Invalid message format:")

            # Force bad command to reach the server
            monkeypatch.setattr("parsec.core.ipcinterface.cmd_req_serializer.dump", lambda x: x)
            with pytest.raises(IPCServerBadResponse) as exc:
                await send_to_ipc_server(file1, "dummy")
            assert exc.value.rep == {
                "status": "invalid_format",
                "reason": "{'cmd': ['Unsupported value: dummy']}",
            }
