# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

# Monitor POC, shamelessly taken from curio

import argparse
import logging
import os
import signal
import socket
import telnetlib
import threading
import traceback

import trio
from trio.abc import Instrument
from trio.hazmat import current_statistics

LOGGER = logging.getLogger("trio.monitor")

MONITOR_HOST = "127.0.0.1"
MONITOR_PORT = 48802

# Telnet doesn't support unicode, so we must rely on ascii art instead :'-(
if 0:
    MID_PREFIX = "├─ "
    MID_CONTINUE = "│  "
    END_PREFIX = "└─ "
else:
    MID_PREFIX = "|- "
    MID_CONTINUE = "|  "
    END_PREFIX = "|_ "
END_CONTINUE = " " * len(END_PREFIX)


def is_shielded_task(task):
    cancel_status = task._cancel_status
    while cancel_status:
        if cancel_status._scope.shield:
            return True
        cancel_status = cancel_status._parent
    return False


def _render_subtree(name, rendered_children):
    lines = []
    lines.append(name)
    for child_lines in rendered_children:
        if child_lines is rendered_children[-1]:
            first_prefix = END_PREFIX
            rest_prefix = END_CONTINUE
        else:
            first_prefix = MID_PREFIX
            rest_prefix = MID_CONTINUE
        lines.append(first_prefix + child_lines[0])
        for child_line in child_lines[1:]:
            lines.append(rest_prefix + child_line)
    return lines


def _rendered_nursery_children(nursery, format_task):
    return [task_tree_lines(t, format_task) for t in nursery.child_tasks]


def task_tree_lines(task, format_task):
    rendered_children = []
    nurseries = list(task.child_nurseries)
    while nurseries:
        nursery = nurseries.pop()
        nursery_children = _rendered_nursery_children(nursery, format_task)
        if rendered_children:
            nested = _render_subtree("(nested nursery)", rendered_children)
            nursery_children.append(nested)
        rendered_children = nursery_children
    return _render_subtree(format_task(task), rendered_children)


def render_task_tree(task, format_task):
    return "\n".join(line for line in task_tree_lines(task, format_task)) + "\n"


class Monitor(Instrument):
    def __init__(self, host=MONITOR_HOST, port=MONITOR_PORT):
        self.address = (host, port)
        self._trio_token = None
        self._next_task_short_id = 0
        self._tasks = {}
        self._closing = None
        self._ui_thread = None

    def get_task_from_short_id(self, shortid):
        for task in self._tasks.values():
            if task._monitor_short_id == shortid:
                return task
        return None

    def before_run(self):
        LOGGER.info("Starting Trio monitor at %s:%d", *self.address)
        self._trio_token = trio.hazmat.current_trio_token()
        self._ui_thread = threading.Thread(target=self.server, args=(), daemon=True)
        self._closing = threading.Event()
        self._ui_thread.start()

    def task_spawned(self, task):
        self._tasks[id(task)] = task
        task._monitor_short_id = self._next_task_short_id
        self._next_task_short_id += 1
        task._monitor_state = "spawned"

    def task_scheduled(self, task):
        task._monitor_state = "scheduled"

    def before_task_step(self, task):
        task._monitor_state = "running"

    def after_task_step(self, task):
        task._monitor_state = "waiting"

    def task_exited(self, task):
        del self._tasks[id(task)]

    # def before_io_wait(self, timeout):
    #     if timeout:
    #         print("### waiting for I/O for up to {} seconds".format(timeout))
    #     else:
    #         print("### doing a quick check for I/O")
    #     self._sleep_time = trio.current_time()

    # def after_io_wait(self, timeout):
    #     duration = trio.current_time() - self._sleep_time
    #     print("### finished I/O check (took {} seconds)".format(duration))

    def after_run(self):
        LOGGER.info("Stopping Trio monitor ui thread")
        self._closing.set()
        self._ui_thread.join()

    def server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # set the timeout to prevent the server loop from
        # blocking indefinitaly on sock.accept()
        sock.settimeout(0.5)
        sock.bind(self.address)
        sock.listen(1)
        with sock:
            while not self._closing.is_set():
                try:
                    client, addr = sock.accept()
                    with client:
                        client.settimeout(0.5)

                        # This bit of magic is for reading lines of input while still allowing
                        # timeouts and the ability for the monitor to die when curio exits.
                        # See Issue #108.

                        def readlines():
                            buffer = bytearray()
                            while not self._closing.is_set():
                                index = buffer.find(b"\n")
                                if index >= 0:
                                    line = buffer[: index + 1].decode("latin-1")
                                    del buffer[: index + 1]
                                    yield line

                                try:
                                    chunk = client.recv(1000)
                                    if not chunk:
                                        break

                                    buffer.extend(chunk)
                                except socket.timeout:
                                    pass

                        sout = client.makefile("w", encoding="latin-1")
                        self.interactive_loop(sout, readlines())
                except socket.timeout:
                    continue

    def interactive_loop(self, sout, input_lines):
        """
        Main interactive loop of the monitor
        """
        sout.write("Trio Monitor: %d tasks running\n" % len(self._tasks))
        sout.write("Type help for commands\n")
        while True:
            sout.write("trio > ")
            sout.flush()
            resp = next(input_lines, None)
            if not resp:
                return

            try:
                if resp.startswith("q"):
                    self.command_exit(sout)
                    return

                elif resp.startswith("pa"):
                    _, taskid_s = resp.split()
                    self.command_parents(sout, int(taskid_s))

                elif resp.startswith("s"):
                    self.command_stats(sout)

                elif resp.startswith("p"):
                    self.command_ps(sout)

                elif resp.startswith("t"):
                    self.command_task_tree(sout)

                elif resp.startswith("exit"):
                    self.command_exit(sout)
                    return

                elif resp.startswith("cancel"):
                    _, taskid_s = resp.split()
                    self.command_cancel(sout, int(taskid_s))

                elif resp.startswith("signal"):
                    _, signame = resp.split()
                    self.command_signal(sout, signame)

                elif resp.startswith("w"):
                    _, taskid_s = resp.split()
                    self.command_where(sout, int(taskid_s))

                elif resp.startswith("h"):
                    self.command_help(sout)
                else:
                    sout.write("Unknown command. Type help.\n")
            except Exception as e:
                sout.write("Bad command. %s\n" % e)

    def command_help(self, sout):
        sout.write(
            """Commands:
         ps               : Show task table
         stat             : Display general runtime informations
         tree             : Display hierarchical view of tasks and nurseries
         where taskid     : Show stack frames for a task
         cancel taskid    : Cancel an indicated task
         signal signame   : Send a Unix signal
         parents taskid   : List task parents
         quit             : Leave the monitor
"""
        )

    def command_stats(self, sout):
        async def get_current_statistics():
            return current_statistics()

        stats = trio.from_thread.run(get_current_statistics, trio_token=self._trio_token)
        sout.write(
            """tasks_living: {s.tasks_living}
tasks_runnable: {s.tasks_runnable}
seconds_to_next_deadline: {s.seconds_to_next_deadline}
run_sync_soon_queue_size: {s.run_sync_soon_queue_size}
io_statistics:
    tasks_waiting_read: {s.io_statistics.tasks_waiting_read}
    tasks_waiting_write: {s.io_statistics.tasks_waiting_write}
    backend: {s.io_statistics.backend}
""".format(
                s=stats
            )
        )

    def command_ps(self, sout):
        headers = ("Id", "State", "Shielded", "Task")
        widths = (5, 10, 10, 50)
        for h, w in zip(headers, widths):
            sout.write("%-*s " % (w, h))
        sout.write("\n")
        sout.write(" ".join(w * "-" for w in widths))
        sout.write("\n")
        for task in sorted(self._tasks.values(), key=lambda t: t._monitor_short_id):
            sout.write(
                "%-*d %-*s %-*s %-*s\n"
                % (
                    widths[0],
                    task._monitor_short_id,
                    widths[1],
                    task._monitor_state,
                    widths[2],
                    "yes" if is_shielded_task(task) else "",
                    widths[3],
                    task.name,
                )
            )

    def command_task_tree(self, sout):
        root_task = next(iter(self._tasks.values()))
        while root_task.parent_nursery is not None:
            root_task = root_task.parent_nursery.parent_task

        def _format_task(task):
            return "%s (id=%s, %s%s)" % (
                task.name,
                task._monitor_short_id,
                task._monitor_state,
                ", shielded" if is_shielded_task(task) else "",
            )

        task_tree = render_task_tree(root_task, _format_task)
        sout.write(task_tree)

    def command_where(self, sout, taskid):
        task = self.get_task_from_short_id(taskid)
        if task:

            def walk_coro_stack(coro):
                while coro is not None:
                    if hasattr(coro, "cr_frame"):
                        # A real coroutine
                        yield coro.cr_frame, coro.cr_frame.f_lineno

                        coro = coro.cr_await
                    elif hasattr(coro, "gi_frame"):
                        # A generator decorated with @types.coroutine
                        yield coro.gi_frame, coro.gi_frame.f_lineno

                        coro = coro.gi_yieldfrom
                    else:
                        # A coroutine wrapper (used by AsyncGenerator for
                        # instance), cannot go further
                        return

            ss = traceback.StackSummary.extract(walk_coro_stack(task.coro))
            tb = "".join(ss.format())
            sout.write(tb + "\n")
        else:
            sout.write("No task %d\n" % taskid)

    def command_signal(self, sout, signame):
        if hasattr(signal, signame):
            os.kill(os.getpid(), getattr(signal, signame))
        else:
            sout.write("Unknown signal %s\n" % signame)

    def command_cancel(self, sout, taskid):
        # TODO: how to cancel a single task ?
        # Another solution could be to also display nurseries/cancel_scopes in
        # the monitor and allow to cancel them. Given timeout are handled
        # by cancel_scope, this could also allow us to monitor the remaining
        # time (and task depending on it) in such object.
        sout.write("Not supported yet...")

    def command_parents(self, sout, taskid):
        task = self.get_task_from_short_id(taskid)
        while task:
            sout.write("%-6d %12s %s\n" % (task._monitor_short_id, "running", task.name))
            task = task.parent_nursery._parent_task if task.parent_nursery else None

    def command_exit(self, sout):
        sout.write("Leaving monitor. Hit Ctrl-C to exit\n")
        sout.flush()


def monitor_client(host, port):
    """
    Client to connect to the monitor via "telnet"
    """
    tn = telnetlib.Telnet()
    tn.open(host, port, timeout=0.5)
    try:
        tn.interact()
    except KeyboardInterrupt:
        pass
    finally:
        tn.close()


def main():
    parser = argparse.ArgumentParser("usage: python -m trio.monitor [options]")
    parser.add_argument(
        "-H", "--host", dest="monitor_host", default=MONITOR_HOST, type=str, help="monitor host ip"
    )

    parser.add_argument(
        "-p",
        "--port",
        dest="monitor_port",
        default=MONITOR_PORT,
        type=int,
        help="monitor port number",
    )
    args = parser.parse_args()
    monitor_client(args.monitor_host, args.monitor_port)


if __name__ == "__main__":
    main()
