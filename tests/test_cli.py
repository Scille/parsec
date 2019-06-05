# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest
import re
import os
from pathlib import Path
from contextlib import contextmanager
from time import sleep
import subprocess
from unittest.mock import ANY, MagicMock, patch

from async_generator import asynccontextmanager
from click.testing import CliRunner


import parsec
from parsec.cli import cli
from parsec.backend.config import DEFAULT_ADMINISTRATION_TOKEN


CWD = Path(__file__).parent.parent


def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert f"parsec, version {parsec.__version__}\n" in result.output


def test_share_workspace(tmpdir, alice, bob):
    # As usual Windows path require a big hack...
    config_dir = tmpdir.strpath.replace("\\", "\\\\")
    # Mocking
    factory_mock = MagicMock()
    share_mock = MagicMock()

    @asynccontextmanager
    async def logged_core_factory(*args, **kwargs):
        yield factory_mock(*args, **kwargs)

    async def share(*args, **kwargs):
        return share_mock(*args, **kwargs)

    factory_mock.return_value.user_fs.workspace_share = share

    with patch("parsec.core.cli.utils.list_available_devices") as list_mock:
        with patch("parsec.core.cli.utils.load_device_with_password") as load_mock:
            with patch("parsec.core.cli.share_workspace.logged_core_factory", logged_core_factory):
                list_mock.return_value = [
                    (bob.organization_id, bob.device_id, "password", MagicMock())
                ]
                runner = CliRunner()
                args = (
                    "core share_workspace --password bla "
                    f"--device={bob.device_id} --config-dir={config_dir} "
                    f"ws1 {alice.user_id}"
                )
                result = runner.invoke(cli, args)

    print(result.output)
    assert result.exit_code == 0
    assert result.output == ""

    list_mock.assert_called_once_with(ANY)
    load_mock.assert_called_once_with(ANY, "bla")
    factory_mock.assert_called_once_with(ANY, load_mock.return_value)
    share_mock.assert_called_once_with("/ws1", alice.user_id)


def _run(cmd):
    print(f"========= RUN {cmd} ==============")
    env = {**os.environ.copy(), "DEBUG": "true"}
    cooked_cmd = ("python -m parsec.cli " + cmd).split()
    ret = subprocess.run(
        cooked_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=CWD, env=env
    )
    print(ret.stdout.decode())
    print(ret.stderr.decode())
    print(f"========= DONE {ret.returncode} ({cmd[:40]}) ==============")
    assert ret.returncode == 0
    return ret


class LivingStream:
    def __init__(self, stream):
        self.stream = stream
        self._already_read_data = ""

    def read(self):
        new_data_size = len(self.stream.peek())
        new_data = self.stream.read(new_data_size).decode()
        self._already_read_data += new_data
        return self._already_read_data


@contextmanager
def _running(cmd, wait_for=None):
    env = {**os.environ.copy(), "DEBUG": "true"}
    cooked_cmd = ("python -m parsec.cli " + cmd).split()
    p = subprocess.Popen(
        cooked_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        cwd=CWD,
        env=env,
    )
    p.live_stdout = LivingStream(p.stdout)
    p.live_stderr = LivingStream(p.stderr)
    try:
        if wait_for:
            out = ""
            for _ in range(10):
                out = p.live_stdout.read()[len(out) :]
                if out:
                    print(out, end="")
                if wait_for in out:
                    break
                sleep(0.1)
            else:
                raise RuntimeError(f"Command took too much time to start")

        yield p

    finally:
        print(f"**************************** TERM to {p.pid} ({cmd[:40]})")
        p.terminate()
        p.wait()
        print(p.live_stdout.read())
        print(p.live_stderr.read())


@pytest.mark.skipif(os.name == "nt", reason="Hard to test on Windows...")
def test_init_backend(postgresql_url, unused_tcp_port):
    _run(f"backend init --db {postgresql_url}")

    # Already initialized db is ok
    p = _run(f"backend init --db {postgresql_url}")
    assert b"Database already initialized" in p.stdout

    # Test backend can run
    with _running(
        ("backend run --blockstore=POSTGRESQL " f"--db={postgresql_url} --port={unused_tcp_port}"),
        wait_for="Starting Parsec Backend",
    ):
        pass


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="Hard to test on Windows...")
def test_full_run(alice, alice2, bob, unused_tcp_port, tmpdir):
    # As usual Windows path require a big hack...
    config_dir = tmpdir.strpath.replace("\\", "\\\\")
    org = alice.organization_id
    alice1_slug = f"{alice.organization_id}:{alice.device_id}"
    alice2_slug = f"{alice2.organization_id}:{alice2.device_id}"
    bob1_slug = f"{bob.organization_id}:{bob.device_id}"
    alice1 = alice.device_id
    alice2 = alice2.device_id
    bob1 = bob.device_id
    password = "P@ssw0rd."

    print("######## START BACKEND #########")
    with _running(f"backend run --port={unused_tcp_port}", wait_for="Starting Parsec Backend"):

        print("####### Create organization #######")
        p = _run(
            "core create_organization "
            f"{org} --addr=ws://localhost:{unused_tcp_port} "
            f"--administration-token={DEFAULT_ADMINISTRATION_TOKEN}"
        )
        url = re.search(
            r"^Bootstrap organization url: (.*)$", p.stdout.decode(), re.MULTILINE
        ).group(1)

        print("####### Bootstrap organization #######")
        _run(
            "core bootstrap_organization "
            f"{alice1} --addr={url} --config-dir={config_dir} --password={password}"
        )

        print("####### Create another user #######")
        with _running(
            "core invite_user "
            f"--config-dir={config_dir} --device={alice1_slug} "
            f"--password={password} {bob1.user_id}",
            wait_for="Invitation token:",
        ) as p:
            stdout = p.live_stdout.read()
            url = re.search(r"^Backend url: (.*)$", stdout, re.MULTILINE).group(1)
            token = re.search(r"^Invitation token: (.*)$", stdout, re.MULTILINE).group(1)

            _run(
                "core claim_user "
                f"--config-dir={config_dir} --addr={url} --token={token} "
                f"--password={password} {bob1}"
            )

        print("####### Create another device #######")
        with _running(
            "core invite_device "
            f"--config-dir={config_dir} --device={alice1_slug} --password={password}"
            f" {alice2.device_name}",
            wait_for="Invitation token:",
        ) as p:
            stdout = p.live_stdout.read()
            url = re.search(r"^Backend url: (.*)$", stdout, re.MULTILINE).group(1)
            token = re.search(r"^Invitation token: (.*)$", stdout, re.MULTILINE).group(1)

            _run(
                "core claim_device "
                f"--config-dir={config_dir} --addr={url} --token={token} "
                f"--password={password} {alice2}"
            )

        print("####### List users #######")
        p = _run(f"core list_devices --config-dir={config_dir}")
        stdout = p.stdout.decode()
        assert alice1 in stdout
        assert alice2 in stdout
        assert bob1 in stdout

        print("####### New users can communicate with backend #######")
        _run(
            "core create_workspace wksp1 "
            f"--config-dir={config_dir} --device={bob1_slug} --password={password}"
        )
        _run(
            "core create_workspace wksp2 "
            f"--config-dir={config_dir} --device={alice2_slug} --password={password}"
        )
