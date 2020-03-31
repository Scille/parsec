# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio
import errno
from pathlib import PurePath
import threading
from cheroot import wsgi
from wsgidav.wsgidav_app import WsgiDAVApp

from parsec.core.mountpoint.thread_fs_access import ThreadFSAccess
from parsec.core.mountpoint.webdav_operations import ParsecDAVProvider
from parsec.core.mountpoint.exceptions import MountpointDriverCrash


async def webdav_mountpoint_runner(
    workspace_fs,
    base_mountpoint_path: PurePath,
    config: dict,
    event_bus,
    *,
    task_status=trio.TASK_STATUS_IGNORED,
):
    """
    Raises:
        MountpointDriverCrash
    """
    host = "127.0.0.1"
    port = 0
    mountpoint_path = f"{base_mountpoint_path}/{workspace_fs.get_workspace_name()}"  # TODO
    webdav_thread_stopped = threading.Event()
    trio_token = trio.hazmat.current_trio_token()
    fs_access = ThreadFSAccess(trio_token, workspace_fs)

    app = WsgiDAVApp(
        config={
            "host": host,
            "port": port,
            "provider_mapping": {"/": ParsecDAVProvider(event_bus, fs_access)},
            "http_authenticator": {
                "domain_controller": "wsgidav.dc.simple_dc.SimpleDomainController",
                "accept_basic": True,
                "accept_digest": True,
                "default_to_digest": True,
            },
            "simple_dc": {"user_mapping": {"*": True}},
            "error_printer": {"catch_all": False},
            # Show http requests in debug, only show error/warnings otherwise
            # "verbose": 3 if config["debug"] else 2,
            "verbose": 5,
        }
    )

    server = wsgi.Server(bind_addr=(host, port), wsgi_app=app)

    try:
        event_bus.send("mountpoint.starting", mountpoint=mountpoint_path)

        async with trio.open_service_nursery() as nursery:

            def _run_webdav_thread():
                print("#######################")
                # logger.info("Starting WebDAV thread...", mountpoint=mountpoint_path)
                try:
                    print("start webdav server")
                    server.start()
                    print("stopped webdav server")

                except Exception as exc:
                    print("huho !", exc)
                    try:
                        errcode = errno.errorcode[exc.args[0]]
                    except (KeyError, IndexError):
                        errcode = f"Unknown error code: {exc}"
                    raise MountpointDriverCrash(
                        f"WebDAV has crashed on {mountpoint_path}: {errcode}"
                    ) from exc

                finally:
                    print("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
                    webdav_thread_stopped.set()

            nursery.start_soon(
                lambda: trio.to_thread.run_sync(_run_webdav_thread, cancellable=True)
            )
            print("wait webdav_thread_started...")
            await _wait_for_webdav_ready(mountpoint_path, server)
            print("webdav_thread_started ok")

            event_bus.send("mountpoint.started", mountpoint=mountpoint_path)
            task_status.started(mountpoint_path)
    except Exception as exc:
        print("ERRRR", exc)
        raise

    finally:

        def _stop_webdav_thread():
            print("stop webdav...")
            server.stop()
            print("wait webdav_thread_stopped...")
            webdav_thread_stopped.wait()
            print("stop webdav ok")

        with trio.CancelScope(shield=True):
            await trio.to_thread.run_sync(_stop_webdav_thread)
        event_bus.send("mountpoint.stopped", mountpoint=mountpoint_path)


async def _wait_for_webdav_ready(mountpoint_path, server):
    while True:
        if getattr(server, "socket", None):
            port = server.socket.getsockname()[1]
            print("===========>", port, mountpoint_path)
            # TODO: poll the webdav endpoint with HTTP HEAD requests
            break
        print("sleep...")
        await trio.sleep(0.01)
        print("end sleep !")
