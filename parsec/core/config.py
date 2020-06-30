# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import json
import os
from pathlib import Path
from typing import FrozenSet, Optional

import attr
from structlog import get_logger

from parsec.api.data import EntryID

logger = get_logger()


def get_default_data_base_dir(environ: dict) -> Path:
    if os.name == "nt":
        return Path(environ["APPDATA"]) / "parsec/data"
    else:
        path = environ.get("XDG_DATA_HOME")
        if not path:
            path = f"{environ['HOME']}/.local/share"
        return Path(path) / "parsec"


def get_default_cache_base_dir(environ: dict) -> Path:
    if os.name == "nt":
        return Path(environ["APPDATA"]) / "parsec/cache"
    else:
        path = environ.get("XDG_CACHE_HOME")
        if not path:
            path = f"{environ.get('HOME')}/.cache"
        return Path(path) / "parsec"


def get_default_config_dir(environ: dict) -> Path:
    if os.name == "nt":
        return Path(environ["APPDATA"]) / "parsec/config"
    else:
        path = environ.get("XDG_CONFIG_HOME")
        if not path:
            path = f"{environ.get('HOME')}/.config"
        return Path(path) / "parsec"


def get_default_mountpoint_base_dir(environ: dict) -> Path:
    return Path.home() / "Parsec"


@attr.s(slots=True, frozen=True, auto_attribs=True)
class CoreConfig:
    config_dir: Path
    data_base_dir: Path
    cache_base_dir: Path
    mountpoint_base_dir: Path

    debug: bool = False

    backend_max_cooldown: int = 30
    backend_connection_keepalive: Optional[int] = 29
    backend_max_connections: int = 4

    invitation_token_size: int = 8

    mountpoint_enabled: bool = False
    disabled_workspaces: FrozenSet[EntryID] = frozenset()

    sentry_url: Optional[str] = None
    telemetry_enabled: bool = True

    gui_last_device: Optional[str] = None
    gui_tray_enabled: bool = True
    gui_language: Optional[str] = None
    gui_first_launch: bool = True
    gui_last_version: Optional[str] = None
    gui_check_version_at_startup: bool = True
    gui_check_version_url: str = "https://github.com/Scille/parsec-cloud/releases/latest"
    gui_check_version_api_url: str = "https://api.github.com/repos/Scille/parsec-cloud/releases"
    gui_confirmation_before_close: bool = True
    gui_workspace_color: bool = False
    gui_allow_multiple_instances: bool = False

    ipc_socket_file: Path = None
    ipc_win32_mutex_name: str = "parsec-cloud"

    def evolve(self, **kwargs):
        return attr.evolve(self, **kwargs)


def config_factory(
    config_dir: Path = None,
    data_base_dir: Path = None,
    cache_base_dir: Path = None,
    mountpoint_base_dir: Path = None,
    mountpoint_enabled: bool = False,
    disabled_workspaces: FrozenSet[EntryID] = frozenset(),
    backend_max_cooldown: int = 30,
    backend_connection_keepalive: Optional[int] = 29,
    backend_max_connections: int = 4,
    telemetry_enabled: bool = True,
    debug: bool = False,
    gui_last_device: str = None,
    gui_tray_enabled: bool = True,
    gui_language: str = None,
    gui_first_launch: bool = True,
    gui_last_version: str = None,
    gui_check_version_at_startup: bool = True,
    gui_workspace_color: bool = False,
    gui_allow_multiple_instances: bool = False,
    environ: dict = {},
    **_,
) -> CoreConfig:
    data_base_dir = data_base_dir or get_default_data_base_dir(environ)
    core_config = CoreConfig(
        config_dir=config_dir or get_default_config_dir(environ),
        data_base_dir=data_base_dir,
        cache_base_dir=cache_base_dir or get_default_cache_base_dir(environ),
        mountpoint_base_dir=get_default_mountpoint_base_dir(environ),
        mountpoint_enabled=mountpoint_enabled,
        disabled_workspaces=disabled_workspaces,
        backend_max_cooldown=backend_max_cooldown,
        backend_connection_keepalive=backend_connection_keepalive,
        backend_max_connections=backend_max_connections,
        telemetry_enabled=telemetry_enabled,
        debug=debug,
        sentry_url=environ.get("SENTRY_URL") or None,
        gui_last_device=gui_last_device,
        gui_tray_enabled=gui_tray_enabled,
        gui_language=gui_language,
        gui_first_launch=gui_first_launch,
        gui_last_version=gui_last_version,
        gui_check_version_at_startup=gui_check_version_at_startup,
        gui_workspace_color=gui_workspace_color,
        gui_allow_multiple_instances=gui_allow_multiple_instances,
        ipc_socket_file=data_base_dir / "parsec-cloud.lock",
        ipc_win32_mutex_name="parsec-cloud",
    )

    # Make sure the directories exist on the system
    core_config.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    core_config.data_base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    core_config.cache_base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Mountpoint base directory is not used on windows
    if os.name != "nt":
        core_config.mountpoint_base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    return core_config


def load_config(config_dir: Path, **extra_config) -> CoreConfig:

    config_file = config_dir / "config.json"
    try:
        raw_conf = config_file.read_text()
        data_conf = json.loads(raw_conf)

    except OSError:
        # Config file not created yet, fallback to default
        data_conf = {}

    except (ValueError, json.JSONDecodeError) as exc:
        # Config file broken, fallback to default
        logger.warning(f"Ignoring invalid config in {config_file} ({exc})")
        data_conf = {}

    try:
        data_conf["data_base_dir"] = Path(data_conf["data_base_dir"])
    except (KeyError, ValueError):
        pass

    try:
        data_conf["cache_base_dir"] = Path(data_conf["cache_base_dir"])
    except (KeyError, ValueError):
        pass

    try:
        data_conf["disabled_workspaces"] = frozenset(map(EntryID, data_conf["disabled_workspaces"]))
    except (KeyError, ValueError):
        pass

    # Work around versionning issue with parsec releases:
    # - v1.12.0, v1.11.4, v1.11.3, v1.11.2, v1.11.1, v1.11.0 and v1.10.0
    # A `v` has been incorrectly added to `parsec.__version__`, potentially
    # affecting the `gui_last_version` entry in the configuration file.
    if data_conf.get("gui_last_version"):
        data_conf["gui_last_version"] = data_conf["gui_last_version"].lstrip("v")

    return config_factory(config_dir=config_dir, **data_conf, **extra_config, environ=os.environ)


def reload_config(config: CoreConfig) -> CoreConfig:
    return load_config(config.config_dir, debug=config.debug)


def save_config(config: CoreConfig):
    config_path = config.config_dir
    config_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path /= "config.json"
    config_path.touch(exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "data_base_dir": str(config.data_base_dir),
                "cache_base_dir": str(config.cache_base_dir),
                "telemetry_enabled": config.telemetry_enabled,
                "disabled_workspaces": list(map(str, config.disabled_workspaces)),
                "backend_max_cooldown": config.backend_max_cooldown,
                "backend_connection_keepalive": config.backend_connection_keepalive,
                "gui_last_device": config.gui_last_device,
                "gui_tray_enabled": config.gui_tray_enabled,
                "gui_language": config.gui_language,
                "gui_first_launch": config.gui_first_launch,
                "gui_last_version": config.gui_last_version,
                "gui_check_version_at_startup": config.gui_check_version_at_startup,
                "gui_workspace_color": config.gui_workspace_color,
                "gui_allow_multiple_instances": config.gui_allow_multiple_instances,
            },
            indent=True,
        )
    )
