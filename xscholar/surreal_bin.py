# -*- coding: utf-8 -*-
"""surreal_bin.py -- find the SurrealDB binary on any OS, or say clearly that it is missing.

Order: $XSCHOLAR_SURREAL, `surreal` on PATH, the Windows per-user install location.
"""
import os
import pathlib
import shutil
import socket


def find():
    candidates = [os.environ.get("XSCHOLAR_SURREAL"), shutil.which("surreal"),
                  str(pathlib.Path.home() / "AppData" / "Local" / "SurrealDB" / "surreal.exe")]
    for path in candidates:
        if path and pathlib.Path(path).exists():
            return str(path)
    return None


MISSING = ("SurrealDB binary not found. Install it (https://surrealdb.com/install) or set "
           "XSCHOLAR_SURREAL to its path.")


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
