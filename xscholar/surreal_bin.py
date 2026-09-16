# -*- coding: utf-8 -*-
"""surreal_bin.py -- find the SurrealDB binary on any OS, or say clearly that it is missing.

Order: $XSCHOLAR_SURREAL, `surreal` on PATH, the Windows per-user install location.
"""
import os
import pathlib
import shutil


def find():
    candidates = [os.environ.get("XSCHOLAR_SURREAL"), shutil.which("surreal"),
                  str(pathlib.Path.home() / "AppData" / "Local" / "SurrealDB" / "surreal.exe")]
    for path in candidates:
        if path and pathlib.Path(path).exists():
            return str(path)
    return None


MISSING = ("SurrealDB binary not found. Install it (https://surrealdb.com/install) or set "
           "XSCHOLAR_SURREAL to its path.")
