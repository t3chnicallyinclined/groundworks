# -*- coding: utf-8 -*-
"""surreal_bin.py -- find the SurrealDB binary on any OS, or say clearly that it is missing; and start a
throwaway in-memory server that is provably OUR server, or refuse.

Order: $GROUNDWORKS_SURREAL, `surreal` on PATH, the Windows per-user install location.

`start_memory` is the one implementation of the throwaway-server start. scripts/run_instance.py and
groundworks/evaluator.py each carried a copy of its guards until 2026-09-16 (docs/TUNING.md, third lane rule: one
rule, one implementation -- a second copy drifts). Test suites still start their own servers in setUpClass;
tests/test_port_guard.py checks each of those refuses an occupied port.
"""
import os
import pathlib
import shutil
import socket
import subprocess
import time
import urllib.request


def find():
    candidates = [os.environ.get("GROUNDWORKS_SURREAL"), shutil.which("surreal"),
                  str(pathlib.Path.home() / "AppData" / "Local" / "SurrealDB" / "surreal.exe")]
    for path in candidates:
        if path and pathlib.Path(path).exists():
            return str(path)
    return None


MISSING = ("SurrealDB binary not found. Install it (https://surrealdb.com/install) or set "
           "GROUNDWORKS_SURREAL to its path.")


def free_port(host="127.0.0.1"):
    """A port the OS reports free right now, for a server this process is about to start.

    Suites used to hard-code ports in 8023-8039 and a registry test kept them apart. Ports collided with
    other agents' servers at least four times in one day, each time as a skipped suite someone had to
    chase. An OS-assigned port comes from the ephemeral range (49152+ on Windows, typically 32768+ on
    Linux), so it can never be a live database's 8001-8003, and two concurrent runs of the same suite no
    longer collide. There is still a window between choosing and binding; start_memory refuses a port
    that is taken by then instead of borrowing it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def port_in_use(port, host="127.0.0.1"):
    """True if something is already listening. A test that finds a stranger on its port and uses it
    anyway is not testing anything: it inherits that database's rows and its schema. Measured once --
    a leftover server on 8031 made seven temporal tests fail with `record already exists`, which is
    the friendly version; the unfriendly version is a test that passes against the wrong database."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def occupied(port):
    """The message to refuse with. Never reuse the server that is already there: its rows, its schema
    and its gates are not the ones under test, and a pass against it means nothing."""
    return ("NOT EXECUTED: port %d is already in use. This starts its own throwaway server and will not "
            "borrow another one. Stop that process by PID (never by name) or choose another port, then "
            "re-run." % port)


SETTLE = 0.5   # how long our own server is given to die before its health answer is believed


class NotStarted(Exception):
    """The throwaway server was not started, or is not provably ours. The message begins NOT EXECUTED."""


def start_memory(port, binary=None, attempts=80):
    """Start `surreal start --bind 127.0.0.1:PORT memory` and return the process once it answers /health AND is
    still alive. Raises NotStarted, never returns a server it cannot prove it started. Stop it with stop(proc),
    by its own handle -- never by name.

    The guards, each from a failure measured on this machine:
      - the port is checked BEFORE starting: without it our `surreal start` fails to bind, the health poll is
        answered by whatever already holds the port, and a whole run reports success against a stranger's
        database (another session held 8025, the port the README demo uses);
      - a process that exits at once is reported, not waited on;
      - after the first health answer, our process is given SETTLE to die and asked again: a stranger who bound
        the port just after the check answers /health while our own process is still failing to bind (measured:
        the poll returned while poll() was still None)."""
    binary = binary or find()
    if not binary:
        raise NotStarted("NOT EXECUTED: " + MISSING)
    if port_in_use(port):
        raise NotStarted(occupied(port))
    proc = subprocess.Popen([str(binary), "start", "--bind", "127.0.0.1:%d" % port, "--user", "root",
                             "--pass", "root", "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(attempts):
        if proc.poll() is not None:
            raise NotStarted("NOT EXECUTED: throwaway SurrealDB on %d exited at once (exit %s); "
                             "something else may hold the port" % (port, proc.returncode))
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=1)
        except Exception:
            time.sleep(0.25)
            continue
        time.sleep(SETTLE)
        if proc.poll() is not None:
            raise NotStarted("NOT EXECUTED: something is answering on %d but our own SurrealDB exited "
                             "(exit %s); the port was taken between the check and the start. Stop that "
                             "process by PID (never by name) or choose another port." % (port, proc.returncode))
        return proc
    stop(proc)
    raise NotStarted("NOT EXECUTED: throwaway SurrealDB did not start on %d" % port)


def stop(proc):
    """Stop a server this process started, by its own handle. Never by name."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
