#! /usr/bin/env python3
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


import signal
import trio
from pathlib import Path
from time import sleep, time
from tempfile import mkdtemp
from subprocess import run, Popen, PIPE
from contextlib import contextmanager
from debug_workspace import main as debug_workspace_main


PORT = 6778
PROXIFIED_PORT = 6779
ORGNAME = "Org42"
TOKEN = "CCDCC27B6108438D99EF8AF5E847C3BB"
DEVICE = "alice@dev1"
PASSWORD = "P@ssw0rd."
BANDWIDTH = 100  # KB/s

PARSEC_CLI = "python -m parsec.cli"
PARSEC_PROFILE_CLI = "python -m cProfile -o bench.prof -m parsec.cli"


def run_cmd(cmd, stdin_bytes=None, capture_output=True):
    print(f"---> {cmd}")
    out = run(cmd.split(), capture_output=capture_output, input=stdin_bytes)
    if out.returncode != 0:
        print(out.stdout.decode())
        print(out.stderr.decode())
        raise RuntimeError(f"Error during command `{cmd}`")
    return out


def run_history(path, confdir, dev_id, max_queries=None, workers=None, result_dict=None):
    print(f"\n\n\n\n\nWORKEERS{workers}\n\n\n\n\n\n")
    max_queries_string = f" --max-queries={max_queries}" if max_queries is not None else ""
    workers_string = f" --max-workers={workers}" if workers is not None else ""
    start_time = time()
    cmd_result = run_cmd(
        f"{PARSEC_CLI} core history {path}"
        f" --config-dir={confdir} --device={dev_id} --password={PASSWORD}"
        f"{max_queries_string}{workers_string}",
        capture_output=False,
    )
    if result_dict is not None:
        result_dict[path] = time() - start_time
    return cmd_result


@contextmanager
def keep_running_cmd(cmd):
    print(f"===> {cmd}")
    process = Popen(cmd.split(), stdout=PIPE, stderr=PIPE)
    sleep(0.2)
    if process.poll():
        print(process.stdout.read().decode())
        print(process.stderr.read().decode())
        raise RuntimeError(f"Command `{cmd}` has stopped with status code {process.returncode}")
    try:
        yield
    finally:
        if not process.poll():
            process.send_signal(signal.SIGINT)
            process.wait()
        if process.returncode != 0:
            print(process.stdout.read().decode())
            print(process.stderr.read().decode())
            raise RuntimeError(f"Command `{cmd}` return status code {process.returncode}")


def show_result(cmd_results):
    print(
        cell_str("lag", filler_num=5),
        cell_str("file name", filler_num=23),
        *[cell_str(f"{w} workers") for w in next(iter(cmd_results.values()))],
    )
    for lag, lag_results in cmd_results.items():
        print("-" * (31 + 25 * len(lag_results)))
        for file_name in next(iter(lag_results.values())):
            print(
                cell_str(lag, filler_num=5),
                cell_str(file_name, filler_num=23),
                *[cell_str(f"{r_by_file[file_name]}") for r_by_file in lag_results.values()],
            )


def cell_str(value, filler_num=23):
    return str(value).rjust(filler_num) + "|"


def main():
    workdir = Path(mkdtemp(prefix="parsec-bench-"))
    print(f"Workdir: {workdir}")
    confdir = workdir / "core"
    mountdir = workdir / "mountpoint"
    confdir.mkdir(exist_ok=True)
    mountdir.mkdir(exist_ok=True)

    backend_addr = f"parsec://127.0.0.1:{PORT}?no_ssl=true"
    proxified_backend_addr = f"parsec://127.0.0.1:{PROXIFIED_PORT}?no_ssl=true"
    # Start backend & create organization

    with keep_running_cmd(
        f"{PARSEC_CLI} backend run --port={PORT} --db=MOCKED -b MOCKED "
        f"--administration-token={TOKEN} --backend-addr={backend_addr} --email-host=MOCKED"
    ):

        with keep_running_cmd(f"toxiproxy-server"):
            run_cmd(
                f"toxiproxy-cli create parsec -l localhost:{PROXIFIED_PORT} -u localhost:{PORT}"
            )

            out = run_cmd(
                f"{PARSEC_CLI} core create_organization {ORGNAME}"
                f" --addr={proxified_backend_addr} --administration-token={TOKEN}"
            )

            boostrap_addr = out.stdout.decode().split("Bootstrap organization url: ")[-1].strip()
            out = run_cmd(
                f"{PARSEC_CLI} core bootstrap_organization {boostrap_addr} "
                f"--config-dir={confdir} --password={PASSWORD}",
                stdin_bytes=b"alice\nalice@dev1\ndev1\n",
            )

            out = run_cmd(f"{PARSEC_CLI} core list_devices --config-dir={confdir}")
            dev_id = out.stdout.split()[5].decode()

            out = run_cmd(
                f"{PARSEC_CLI} core create_workspace w1"
                f" --config-dir={confdir} --device={dev_id} --password={PASSWORD}"
            )

            trio.run(debug_workspace_main, confdir, True, "alice <alice@dev1>", PASSWORD)

            cmd_results = {}
            for lag in [0, 10, 50]:
                cmd_results[lag] = {}
                run_cmd(f"toxiproxy-cli toxic add parsec -t latency -a latency={lag} -n lagparsec")
                run_cmd(
                    f"toxiproxy-cli toxic add parsec -t bandwidth -a rate={BANDWIDTH} "
                    f"-n bandwithparsec"
                )
                # for workers in [1, 3, 10]:
                for workers in [3, 0]:
                    cmd_results[lag][workers] = result_dict = {}
                    run_history("w1:/", confdir, dev_id, workers=workers, result_dict=result_dict)
                    run_history(
                        "w1:/foo", confdir, dev_id, workers=workers, result_dict=result_dict
                    )
                    run_history(
                        "w1:/foo/foo", confdir, dev_id, workers=workers, result_dict=result_dict
                    )
                    run_history(
                        "w1:/foo/foo/foo", confdir, dev_id, workers=workers, result_dict=result_dict
                    )

                    # Shows only one file with one version
                    # yet in a dir where a lot of changes happened
                    run_history(
                        "w1:/foo/foo/foo/foo.txt",
                        confdir,
                        dev_id,
                        workers=workers,
                        max_queries=5,
                        result_dict=result_dict,
                    )
                run_cmd(f"toxiproxy-cli toxic delete parsec -n lagparsec")
                run_cmd(f"toxiproxy-cli toxic delete parsec -n bandwithparsec")

            show_result(cmd_results)


if __name__ == "__main__":
    main()
