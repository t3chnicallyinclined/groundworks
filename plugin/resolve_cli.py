# -*- coding: utf-8 -*-
"""resolve_cli.py -- tiny CLI wrapper so the xsp:init skill stub can shell out to resolve.py."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve import resolve  # noqa: E402


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    print(json.dumps(resolve(start)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
