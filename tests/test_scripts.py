# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import pathlib
import re
import subprocess
import tempfile

import psutil
import pytest

from parsec.core.config import config_factory
from parsec.core.local_device import list_available_devices


def kill_local_backend(backend_port=6888):
    pattern = f"parsec.* backend.* run.* -P {backend_port}"
    for proc in psutil.process_iter():
        if "python" in proc.name():
            arguments = " ".join(proc.cmdline())
            if re.search(pattern, arguments):
                proc.kill()


@pytest.fixture
def run_testenv():
    try:
        # Source the run_testenv script and echo the testenv path
        base_dir = os.path.dirname(__file__)
        if os.name == "nt":
            fd, bat_script = tempfile.mkstemp(suffix=".bat")
            with open(fd, "w") as f:
                f.write(f"call {base_dir}\\scripts\\run_testenv.bat\r\necho %APPDATA%")
            output = subprocess.check_output(str(bat_script))
            os.unlink(bat_script)
        else:
            output = subprocess.check_output(
                f"source {base_dir}/scripts/run_testenv.sh && echo $XDG_CONFIG_HOME",
                shell=True,
                executable="bash",
            )

        # Retrieve the testenv path
        testenv_path = pathlib.Path(output.splitlines()[-1].decode())
        if os.name == "nt":
            data_path = testenv_path / "parsec" / "data"
            cache_path = testenv_path / "parsec" / "cache"
            config_path = testenv_path / "parsec" / "config"
        else:
            testenv_path = testenv_path.parent
            data_path = testenv_path / "share" / "parsec"
            cache_path = testenv_path / "cache" / "parsec"
            config_path = testenv_path / "config" / "parsec"

        # Make sure the corresponding directories exist
        assert testenv_path.exists()
        assert data_path.exists()
        assert cache_path.exists()
        assert config_path.exists()

        # Return a core configuration
        yield config_factory(
            config_dir=config_path, data_base_dir=data_path, cache_base_dir=cache_path
        )

    # Make sure we don't leave a backend running as it messes up with the CI
    finally:
        kill_local_backend()


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="causes a freeze in appveyor for some reasons")
def test_run_testenv(run_testenv):
    available_devices = list_available_devices(run_testenv.config_dir)
    devices = [(d.human_handle.label, d.device_label) for d in available_devices]
    assert sorted(devices) == [("Alice", "laptop"), ("Alice", "pc"), ("Bob", "laptop")]
