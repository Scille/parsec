# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os
import re
import subprocess
import sys
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from time import sleep
from unittest.mock import ANY, MagicMock, patch

import attr
import pytest
import trustme
from async_generator import asynccontextmanager
from click.testing import CliRunner

from parsec import __version__ as parsec_version
from parsec.api.protocol import DeviceID, OrganizationID
from parsec.backend.postgresql import MigrationItem
from parsec.cli import cli
from parsec.core.local_device import list_available_devices, save_device_with_password

try:
    import fcntl
except ModuleNotFoundError:  # Not available on Windows
    pass


CWD = Path(__file__).parent.parent


def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert f"parsec, version {parsec_version}\n" in result.output


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

    password = "S3cr3t"
    save_device_with_password(Path(config_dir), bob, password)

    with patch("parsec.core.cli.share_workspace.logged_core_factory", logged_core_factory):
        runner = CliRunner()
        args = (
            f"core share_workspace --password {password} "
            f"--device={bob.slughash} --config-dir={config_dir} "
            f"ws1 {alice.user_id}"
        )
        result = runner.invoke(cli, args)

    print(result.output)
    assert result.exit_code == 0
    assert result.output == ""

    factory_mock.assert_called_once_with(ANY, bob)
    share_mock.assert_called_once_with("/ws1", alice.user_id)


def _short_cmd(cmd):
    if len(cmd) < 40:
        return cmd
    else:
        return f"{cmd[:40]}…"


def _run(cmd, env={}, timeout=20.0, capture=True):
    print(f"========= RUN {cmd} ==============")
    env = {**os.environ.copy(), "DEBUG": "true", **env}
    cooked_cmd = ("python -m parsec.cli " + cmd).split()
    kwargs = {}
    if capture:
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    ret = subprocess.run(cooked_cmd, cwd=CWD, env=env, timeout=timeout, **kwargs)
    if capture:
        print(ret.stdout.decode(), file=sys.stdout)
        print(ret.stderr.decode(), file=sys.stderr)
    print(f"========= DONE {ret.returncode} ({_short_cmd(cmd)}) ==============")
    assert ret.returncode == 0
    return ret


class LivingStream:
    def __init__(self, stream):
        self.stream = stream
        self._already_read_data = ""

    def read(self):
        new_data_size = len(self.stream.peek())
        new_data = self.stream.read1(new_data_size).decode()
        self._already_read_data += new_data
        return self._already_read_data


@contextmanager
def _running(cmd, wait_for=None, env={}):
    env = {**os.environ.copy(), "DEBUG": "true", **env}
    cooked_cmd = ("python -m parsec.cli " + cmd).split()
    p = subprocess.Popen(
        cooked_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        cwd=CWD,
        env=env,
    )
    p.wait_for = partial(_wait_for, p)
    p.wait_for_regex = partial(_wait_for_regex, p)
    p.live_stdout = LivingStream(p.stdout)
    p.live_stderr = LivingStream(p.stderr)
    # Turn stdin non-blocking
    fl = fcntl.fcntl(p.stdin, fcntl.F_GETFL)
    fcntl.fcntl(p.stdin, fcntl.F_SETFL, fl | os.O_NONBLOCK)
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
        print(f"**************************** TERM to {p.pid} ({_short_cmd(cmd)})")
        p.terminate()
        p.wait()
        print(p.live_stdout.read())
        print(p.live_stderr.read())


def _wait_for(p, wait_txt):
    for _ in range(10):
        sleep(0.1)
        stdout = p.live_stdout.read()
        if wait_txt in stdout:
            return stdout
    else:
        raise AssertionError("Too slow")


def _wait_for_regex(p, regex):
    for _ in range(10):
        sleep(0.1)
        stdout = p.live_stdout.read()
        match = re.search(regex, stdout, re.MULTILINE)
        if match:
            return match
    else:
        raise AssertionError("Too slow")


@pytest.mark.skipif(os.name == "nt", reason="Hard to test on Windows...")
def test_migrate_backend(postgresql_url, unused_tcp_port):
    sql = "SELECT current_database();"  # Dummy migration content
    dry_run_args = f"backend migrate --db {postgresql_url} --dry-run"
    apply_args = f"backend migrate --db {postgresql_url}"

    with patch("parsec.backend.cli.migration.retrieve_migrations") as retrieve_migrations:
        retrieve_migrations.return_value = [
            MigrationItem(
                idx=100001, name="migration1", file_name="100001_migration1.sql", sql=sql
            ),
            MigrationItem(
                idx=100002, name="migration2", file_name="100002_migration2.sql", sql=sql
            ),
        ]
        runner = CliRunner()
        result = runner.invoke(cli, dry_run_args)
        assert "100001_migration1.sql ✔" in result.output
        assert "100002_migration2.sql ✔" in result.output

        runner = CliRunner()
        result = runner.invoke(cli, apply_args)
        assert "100001_migration1.sql ✔" in result.output
        assert "100002_migration2.sql ✔" in result.output

        retrieve_migrations.return_value.append(
            MigrationItem(idx=100003, name="migration3", file_name="100003_migration3.sql", sql=sql)
        )

        result = runner.invoke(cli, dry_run_args)
        assert "100001_migration1.sql (already applied)" in result.output
        assert "100002_migration2.sql (already applied)" in result.output
        assert "100003_migration3.sql ✔" in result.output

        result = runner.invoke(cli, apply_args)
        assert "100001_migration1.sql (already applied)" in result.output
        assert "100002_migration2.sql (already applied)" in result.output
        assert "100003_migration3.sql ✔" in result.output

        result = runner.invoke(cli, apply_args)
        assert "100001_migration1.sql (already applied)" in result.output
        assert "100002_migration2.sql (already applied)" in result.output
        assert "100003_migration3.sql (already applied)" in result.output


@pytest.fixture(params=(False, True), ids=("no_ssl", "ssl"))
def ssl_conf(request):
    @attr.s
    class SSLConf:
        @property
        def use_ssl(self):
            return bool(self.backend_opts)

        client_env = attr.ib(factory=dict)
        backend_opts = attr.ib(default="")

    if not request.param:
        yield SSLConf()
    else:
        ca = trustme.CA()
        server_cert = ca.issue_cert("localhost")
        with ca.cert_pem.tempfile() as ca_certfile, server_cert.cert_chain_pems[
            0
        ].tempfile() as server_certfile, server_cert.private_key_pem.tempfile() as server_keyfile:

            yield SSLConf(
                backend_opts=f" --ssl-keyfile={server_keyfile} --ssl-certfile={server_certfile} ",
                client_env={"SSL_CAFILE": ca_certfile},
            )


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="Hard to test on Windows...")
def test_apiv1_full_run(unused_tcp_port, tmpdir, ssl_conf):
    # As usual Windows path require a big hack...
    config_dir = tmpdir.strpath.replace("\\", "\\\\")
    org = OrganizationID("org")

    # slughash depends on root verify key, so we cannot determine
    # it before the organization is created
    def _retrieve_device_slughash(device_id):
        availables = list_available_devices(Path(config_dir))
        for available in availables:
            if available.device_id == device_id:
                return available.slughash
        else:
            assert False, f"`{device_id}` not among {availables}"

    alice1 = DeviceID("alice@pc1")
    alice2 = DeviceID("alice@pc2")
    bob1 = DeviceID("bob@laptop")
    password = "P@ssw0rd."
    administration_token = "9e57754ddfe62f7f8780edc0"

    print("######## START BACKEND #########")
    with _running(
        (
            f"backend run --db=MOCKED --blockstore=MOCKED"
            f" --administration-token={administration_token}"
            f" --port={unused_tcp_port}"
            f" {ssl_conf.backend_opts}"
        ),
        wait_for="Starting Parsec Backend",
    ):

        print("####### Create organization #######")
        admin_url = f"parsec://localhost:{unused_tcp_port}"
        if not ssl_conf.use_ssl:
            admin_url += "?no_ssl=true"

        p = _run(
            "core create_organization "
            f"{org} --addr={admin_url} "
            f"--administration-token={administration_token}",
            env=ssl_conf.client_env,
        )
        url = re.search(
            r"^Bootstrap organization url: (.*)$", p.stdout.decode(), re.MULTILINE
        ).group(1)

        print("####### Bootstrap organization #######")
        _run(
            "core apiv1 bootstrap_organization "
            f"{alice1} --addr={url} --config-dir={config_dir} --password={password}",
            env=ssl_conf.client_env,
        )

        alice1_slughash = _retrieve_device_slughash(alice1)

        print("####### Create another user #######")
        with _running(
            "core apiv1 invite_user "
            f"--config-dir={config_dir} --device={alice1_slughash} "
            f"--password={password} {bob1.user_id}",
            wait_for="token:",
            env=ssl_conf.client_env,
        ) as p:
            stdout = p.live_stdout.read()
            url = re.search(r"^url: (.*)$", stdout, re.MULTILINE).group(1)
            token = re.search(r"^token: (.*)$", stdout, re.MULTILINE).group(1)

            _run(
                "core apiv1 claim_user "
                f"--config-dir={config_dir} --addr={url} --token={token} "
                f"--password={password} {bob1.device_name}",
                env=ssl_conf.client_env,
            )

            p.wait()

        print("####### Create another device #######")
        with _running(
            "core apiv1 invite_device "
            f"--config-dir={config_dir} --device={alice1_slughash} --password={password}"
            f" {alice2.device_name}",
            wait_for="token:",
            env=ssl_conf.client_env,
        ) as p:
            stdout = p.live_stdout.read()
            url = re.search(r"^url: (.*)$", stdout, re.MULTILINE).group(1)
            token = re.search(r"^token: (.*)$", stdout, re.MULTILINE).group(1)

            _run(
                "core apiv1 claim_device "
                f"--config-dir={config_dir} --addr={url} --token={token} "
                f"--password={password}",
                env=ssl_conf.client_env,
            )

            p.wait()

        alice2_slughash = _retrieve_device_slughash(alice2)
        bob1_slughash = _retrieve_device_slughash(bob1)

        print("####### List users #######")
        p = _run(f"core list_devices --config-dir={config_dir}", env=ssl_conf.client_env)
        stdout = p.stdout.decode()
        assert alice1_slughash[:3] in stdout
        assert f"{org}: {alice1.user_id} @ {alice1.device_name}" in stdout
        assert alice2_slughash[:3] in stdout
        assert f"{org}: {alice2.user_id} @ {alice2.device_name}" in stdout
        assert bob1_slughash[:3] in stdout
        assert f"{org}: {bob1.user_id} @ {bob1.device_name}" in stdout

        print("####### New users can communicate with backend #######")
        _run(
            "core create_workspace wksp1 "
            f"--config-dir={config_dir} --device={bob1_slughash} --password={password}",
            env=ssl_conf.client_env,
        )
        _run(
            "core create_workspace wksp2 "
            f"--config-dir={config_dir} --device={alice2_slughash} --password={password}",
            env=ssl_conf.client_env,
        )

        print("####### Stats organization #######")
        _run(
            "core stats_organization "
            f"{org} --addr={admin_url} "
            f"--administration-token={administration_token}",
            env=ssl_conf.client_env,
        )

        print("####### Status organization #######")
        _run(
            "core status_organization "
            f"{org} --addr={admin_url} "
            f"--administration-token={administration_token}",
            env=ssl_conf.client_env,
        )


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="Hard to test on Windows...")
def test_full_run(coolorg, unused_tcp_port, tmpdir, ssl_conf):
    # As usual Windows path require a big hack...
    config_dir = tmpdir.strpath.replace("\\", "\\\\")
    org = coolorg.organization_id

    alice_human_handle_label = "Alice"
    alice_human_handle_email = "alice@example.com"
    bob_human_handle_label = "Bob"
    bob_human_handle_email = "bob@example.com"
    alice1_device_label = "PC1"
    alice2_device_label = "PC2"
    bob_device_label = "Desktop"

    password = "P@ssw0rd."
    administration_token = "9e57754ddfe62f7f8780edc0"

    print("######## START BACKEND #########")
    with _running(
        (
            f"backend run --db=MOCKED --blockstore=MOCKED"
            f" --administration-token={administration_token}"
            f" --port={unused_tcp_port}"
            f" {ssl_conf.backend_opts}"
        ),
        wait_for="Starting Parsec Backend",
    ):

        print("####### Create organization #######")
        admin_url = f"parsec://localhost:{unused_tcp_port}"
        if not ssl_conf.use_ssl:
            admin_url += "?no_ssl=true"

        p = _run(
            "core create_organization "
            f"{org} --addr={admin_url} "
            f"--administration-token={administration_token}",
            env=ssl_conf.client_env,
        )
        url = re.search(
            r"^Bootstrap organization url: (.*)$", p.stdout.decode(), re.MULTILINE
        ).group(1)

        print("####### Bootstrap organization #######")
        with _running(
            "core bootstrap_organization " f"{url} --config-dir={config_dir} --password={password}",
            env=ssl_conf.client_env,
            wait_for="User fullname:",
        ) as p:
            print("~~~ Bootstrap user fullname ~~~")
            p.stdin.write(f"{alice_human_handle_label}\n".encode())
            p.stdin.flush()
            print("~~~ Bootstrap user email ~~~")
            p.wait_for("User email:")
            p.stdin.write(f"{alice_human_handle_email}\n".encode())
            p.stdin.flush()
            print("~~~ Bootstrap device label ~~~")
            p.wait_for("Device label [")
            p.stdin.write(f"{alice1_device_label}\n".encode())
            p.stdin.flush()
            print("~~~ Bootstrap finish invitation ~~~")
            match = p.wait_for_regex(r"Saving device ([a-zA-Z0-9]+)")
            alice1_slughash = match.group(1)
            p.wait()

        print("####### Stats organization #######")
        _run(
            "core stats_organization "
            f"{org} --addr={admin_url} "
            f"--administration-token={administration_token}",
            env=ssl_conf.client_env,
        )

        print("####### Status organization #######")
        _run(
            "core status_organization "
            f"{org} --addr={admin_url} "
            f"--administration-token={administration_token}",
            env=ssl_conf.client_env,
        )

        print("####### Create user&device invitations #######")
        p = _run(
            "core invite_user "
            f"--config-dir={config_dir} --device={alice1_slughash} "
            f"--password={password} bob@example.com",
            env=ssl_conf.client_env,
        )
        user_invitation_url = re.search(r"^url: (.*)$", p.stdout.decode(), re.MULTILINE).group(1)
        user_invitation_token = re.search(r"token=([^&]+)", user_invitation_url).group(1)

        p = _run(
            "core invite_device "
            f"--config-dir={config_dir} --device={alice1_slughash} "
            f"--password={password}",
            env=ssl_conf.client_env,
        )
        device_invitation_url = re.search(r"^url: (.*)$", p.stdout.decode(), re.MULTILINE).group(1)
        device_invitation_token = re.search(r"token=([^&]+)", device_invitation_url).group(1)

        print("####### List invitations #######")

        p = _run(
            "core list_invitations "
            f"--config-dir={config_dir} --device={alice1_slughash} "
            f"--password={password}",
            env=ssl_conf.client_env,
        )
        stdout = p.stdout.decode()
        assert device_invitation_token in stdout
        assert user_invitation_token in stdout

        print("####### Claim user invitation #######")
        with _running(
            "core claim_invitation "
            f"--config-dir={config_dir} --password={password} {user_invitation_url} ",
            env=ssl_conf.client_env,
        ) as p_claimer:
            with _running(
                "core greet_invitation "
                f"--config-dir={config_dir} --device={alice1_slughash} "
                f"--password={password} {user_invitation_token}",
                env=ssl_conf.client_env,
            ) as p_greeter:

                greeter_code = None

                print("~~~ Retrieve greeter code ~~~")
                match = p_greeter.wait_for_regex(r"Code to provide to claimer: (.*)$")
                greeter_code = match.group(1)

                print("~~~ Provide greeter code ~~~")
                stdout_claimer = p_claimer.wait_for("Select code provided by greeter")
                assert greeter_code in stdout_claimer
                code_index = re.search(
                    f"([0-9]) - {greeter_code}", stdout_claimer, re.MULTILINE
                ).group(1)

                p_claimer.stdin.write(f"{code_index}\n".encode())
                p_claimer.stdin.flush()

                claimer_code = None

                print("~~~ Retrieve claimer code ~~~")
                match = p_claimer.wait_for_regex(r"Code to provide to greeter: (.*)$")
                claimer_code = match.group(1)

                print("~~~ Provide claimer code ~~~")
                stdout_greeter = p_greeter.wait_for("Select code provided by claimer")
                assert claimer_code in stdout_greeter
                code_index = re.search(
                    f"([0-9]) - {claimer_code}", stdout_greeter, re.MULTILINE
                ).group(1)

                p_greeter.stdin.write(f"{code_index}\n".encode())
                p_greeter.stdin.flush()

                print("~~~ Claimer fill info ~~~")
                stdout_claimer = p_claimer.wait_for("User fullname")
                p_claimer.stdin.write(b"John Doe\n")
                p_claimer.stdin.flush()

                stdout_claimer = p_claimer.wait_for("Device label")
                p_claimer.stdin.write(b"D3vIce1\n")
                p_claimer.stdin.flush()

                print("~~~ Greeter validate info ~~~")
                stdout_greeter = p_greeter.wait_for("New user label [John Doe]:")
                p_greeter.stdin.write(f"{bob_human_handle_label}\n".encode())
                p_greeter.stdin.flush()

                stdout_greeter = p_greeter.wait_for("New user email [bob@example.com]:")
                p_greeter.stdin.write(f"{bob_human_handle_email}\n".encode())
                p_greeter.stdin.flush()

                stdout_greeter = p_greeter.wait_for("New user device label [D3vIce1]:")
                p_greeter.stdin.write(f"{bob_device_label}\n".encode())
                p_greeter.stdin.flush()

                stdout_greeter = p_greeter.wait_for("New user profile (0, 1, 2) [0]:")
                p_greeter.stdin.write(b"1\n")
                p_greeter.stdin.flush()

                print("~~~ Greeter finish invitation ~~~")
                p_greeter.wait()

                print("~~~ Claimer finish invitation ~~~")
                match = p_claimer.wait_for_regex(r"Saving device ([a-zA-Z0-9]+)")
                bob1_slughash = match.group(1)
                p_claimer.wait()

        print("####### Claim device invitation #######")
        with _running(
            "core claim_invitation "
            f"--config-dir={config_dir} --password={password} {device_invitation_url} ",
            env=ssl_conf.client_env,
        ) as p_claimer:
            with _running(
                "core greet_invitation "
                f"--config-dir={config_dir} --device={alice1_slughash} "
                f"--password={password} {device_invitation_token}",
                env=ssl_conf.client_env,
            ) as p_greeter:

                greeter_code = None

                print("~~~ Retrieve greeter code ~~~")
                match = p_greeter.wait_for_regex(r"Code to provide to claimer: (.*)$")
                greeter_code = match.group(1)

                print("~~~ Provide greeter code ~~~")
                stdout_claimer = p_claimer.wait_for("Select code provided by greeter")
                assert greeter_code in stdout_claimer
                code_index = re.search(
                    f"([0-9]) - {greeter_code}", stdout_claimer, re.MULTILINE
                ).group(1)

                p_claimer.stdin.write(f"{code_index}\n".encode())
                p_claimer.stdin.flush()

                claimer_code = None

                print("~~~ Retrieve claimer code ~~~")
                match = p_claimer.wait_for_regex(r"Code to provide to greeter: (.*)$")
                claimer_code = match.group(1)

                print("~~~ Provide claimer code ~~~")
                stdout_greeter = p_greeter.wait_for("Select code provided by claimer")
                assert claimer_code in stdout_greeter
                code_index = re.search(
                    rf"([0-9]) - {claimer_code}", stdout_greeter, re.MULTILINE
                ).group(1)
                p_greeter.stdin.write(f"{code_index}\n".encode())
                p_greeter.stdin.flush()

                print("~~~ Claimer fill info ~~~")
                stdout_claimer = p_claimer.wait_for("Device label [")
                p_claimer.stdin.write(b"DeviceB\n")
                p_claimer.stdin.flush()

                print("~~~ Greeter validate info ~~~")
                stdout_greeter = p_greeter.wait_for("New device label [DeviceB]:")
                p_greeter.stdin.write(f"{alice2_device_label}\n".encode())
                p_greeter.stdin.flush()

                print("~~~ Greeter finish invitation ~~~")
                p_greeter.wait()

                print("~~~ Claimer finish invitation ~~~")
                match = p_claimer.wait_for_regex(r"Saving device ([a-zA-Z0-9]+)")
                alice2_slughash = match.group(1)
                p_claimer.wait()

        print("####### List users #######")
        p = _run(f"core list_devices --config-dir={config_dir}", env=ssl_conf.client_env)
        stdout = p.stdout.decode()
        assert alice1_slughash[:3] in stdout
        assert (
            f"{org}: {alice_human_handle_label} <{alice_human_handle_email}> @ {alice1_device_label}"
            in stdout
        )
        assert alice2_slughash[:3] in stdout
        assert (
            f"{org}: {alice_human_handle_label} <{alice_human_handle_email}> @ {alice2_device_label}"
            in stdout
        )
        assert bob1_slughash[:3] in stdout
        assert (
            f"{org}: {bob_human_handle_label} <{bob_human_handle_email}> @ {bob_device_label}"
            in stdout
        )

        print("####### New users can communicate with backend #######")
        _run(
            "core create_workspace wksp1 "
            f"--config-dir={config_dir} --device={bob1_slughash} --password={password}",
            env=ssl_conf.client_env,
        )
        _run(
            "core create_workspace wksp2 "
            f"--config-dir={config_dir} --device={alice2_slughash} --password={password}",
            env=ssl_conf.client_env,
        )


@pytest.mark.gui
@pytest.mark.slow
@pytest.mark.parametrize(
    "env",
    [
        pytest.param({}, id="Standard environement"),
        pytest.param(
            {"WINFSP_LIBRARY_PATH": "nope"},
            id="Wrong winfsp library path",
            marks=pytest.mark.skipif(os.name != "nt", reason="Windows only"),
        ),
        pytest.param(
            {"WINFSP_DEBUG_PATH": "nope"},
            id="Wrong winfsp binary path",
            marks=pytest.mark.skipif(os.name != "nt", reason="Windows only"),
        ),
    ],
)
def test_gui_with_diagnose_option(env):
    _run(f"core gui --diagnose", env=env, capture=False)
