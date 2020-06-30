# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import platform
from contextlib import contextmanager
from enum import Enum
from functools import partial
from pathlib import Path

import trio
from async_generator import asynccontextmanager
from structlog import get_logger

from parsec.serde import (
    BaseSchema,
    MsgpackSerializer,
    OneOfSchema,
    SerdeError,
    Unpacker,
    fields,
    packb,
)

logger = get_logger()


class IPCCommand(Enum):
    FOREGROUND = "foreground"
    NEW_INSTANCE = "new_instance"


class IPCServerError(Exception):
    pass


class IPCServerBadResponse(IPCServerError):
    def __init__(self, rep):
        self.rep = rep

    def __repr__(self):
        return f"Bad response from IPC server: {self.rep}"


class IPCServerNotRunning(IPCServerError):
    pass


class IPCServerAlreadyRunning(IPCServerError):
    pass


class ForegroundReqSchema(BaseSchema):
    cmd = fields.EnumCheckedConstant(IPCCommand.FOREGROUND, required=True)


class NewInstanceReqSchema(BaseSchema):
    cmd = fields.EnumCheckedConstant(IPCCommand.NEW_INSTANCE, required=True)
    start_arg = fields.String(allow_none=True)


class CommandReqSchema(OneOfSchema):
    type_field = "cmd"
    type_schemas = {
        IPCCommand.FOREGROUND: ForegroundReqSchema,
        IPCCommand.NEW_INSTANCE: NewInstanceReqSchema,
    }

    def get_obj_type(self, obj):
        return obj["cmd"]


class CommandRepSchema(BaseSchema):
    status = fields.String(required=True)
    reason = fields.String(allow_none=True)


cmd_req_serializer = MsgpackSerializer(CommandReqSchema)
cmd_rep_serializer = MsgpackSerializer(CommandRepSchema)


DEFAULT_WIN32_MUTEX_NAME = "parsec-cloud"


@contextmanager
def _install_win32_mutex(mutex_name: str):

    from parsec.win32 import CreateMutex, CloseHandle, GetLastError, ERROR_ALREADY_EXISTS

    try:
        mutex = CreateMutex(None, False, mutex_name)
    except WindowsError as exc:
        raise IPCServerError(f"Cannot create mutex `{mutex_name}`: {exc}") from exc
    status = GetLastError()
    if status == ERROR_ALREADY_EXISTS:
        CloseHandle(mutex)
        raise IPCServerAlreadyRunning(f"Mutex `{mutex_name}` already exists")

    try:
        yield

    finally:
        CloseHandle(mutex)


@contextmanager
def _install_posix_file_lock(socket_file: Path):

    import fcntl

    try:
        socket_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(socket_file, "a") as fd:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise IPCServerAlreadyRunning(f"Cannot lock file `{socket_file}`: {exc}") from exc
            yield
            # Lock is released on file descriptor closing
    except OSError as exc:
        raise IPCServerError(f"Cannot create lock file `{socket_file}`: {exc}") from exc


@asynccontextmanager
async def run_ipc_server(
    cmd_handler, socket_file: Path, win32_mutex_name: str = DEFAULT_WIN32_MUTEX_NAME
):
    if platform.system() == "Windows":
        with _install_win32_mutex(win32_mutex_name):
            async with _run_tcp_server(socket_file, cmd_handler):
                yield
    else:
        with _install_posix_file_lock(socket_file):
            async with _run_tcp_server(socket_file, cmd_handler):
                yield


@asynccontextmanager
async def _run_tcp_server(socket_file: Path, cmd_handler):
    async def _client_handler(stream):

        # General exception handling
        try:

            # Stream handling
            try:

                unpacker = Unpacker()
                async for raw in stream:
                    unpacker.feed(raw)
                    for cmd in unpacker:
                        cmd = cmd_req_serializer.load(cmd)
                        rep = await cmd_handler(cmd)
                        raw_rep = cmd_rep_serializer.dumps(rep)
                        logger.info("Command processed", cmd=cmd["cmd"], rep_status=rep["status"])
                        await stream.send_all(raw_rep)

            except SerdeError as exc:
                await stream.send_all(packb({"status": "invalid_format", "reason": str(exc)}))

            finally:
                await stream.aclose()

        except trio.BrokenResourceError:
            pass  # Peer has closed the connection while we were sending a response

        except Exception:
            logger.exception("Unexpected crash")

    try:
        async with trio.open_service_nursery() as nursery:
            listeners = await nursery.start(
                partial(trio.serve_tcp, _client_handler, 0, host="127.0.0.1")
            )
            port = listeners[0].socket.getsockname()[1]

            # Make sure the path exists and write the socket file
            socket_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            socket_file.write_text(str(port))

            logger.info("IPC server ready", port=port)
            try:
                yield
            finally:
                nursery.cancel_scope.cancel()

    except OSError as exc:
        raise IPCServerError(f"Cannot start IPC server: {exc}") from exc


async def send_to_ipc_server(socket_file: Path, cmd, **kwargs):
    try:
        socket_port = int(socket_file.read_text().strip())

    except (ValueError, OSError) as exc:
        raise IPCServerNotRunning("Invalid IPC socket file") from exc

    try:
        stream = await trio.open_tcp_stream("127.0.0.1", socket_port)

        raw_req = cmd_req_serializer.dumps({"cmd": cmd, **kwargs})
        await stream.send_all(raw_req)
        unpacker = Unpacker(exc_cls=IPCServerError)
        while True:
            raw = await stream.receive_some(1000)
            if not raw:
                raise IPCServerError(f"IPC server has closed the connection unexpectly")
            unpacker.feed(raw)
            raw_rep = next(unpacker, None)
            rep = cmd_rep_serializer.load(raw_rep)
            if rep:
                if rep["status"] != "ok":
                    raise IPCServerBadResponse(rep)
                return rep

    except SerdeError as exc:
        raise IPCServerError(f"Invalid message format: {exc}") from exc

    except (OSError, trio.BrokenResourceError) as exc:
        raise IPCServerNotRunning(f"Impossible to connect to IPC server: {exc}") from exc
