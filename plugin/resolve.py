# -*- coding: utf-8 -*-
"""resolve.py -- shared walk-up resolution for the xsp plugin skeleton.

Stub only: no real knowledge-graph logic yet. This is the ONE place that knows the on-disk
marker for "this directory is an xsp root", so the SessionStart hook and the MCP `where` tool
agree by construction instead of by convention.

Layout convention (see HANDOFF-2026-09-17-product-design.md, Packaging/Layout):
  A directory is an xsp root if it contains a `.xsp/` subdirectory.
  That `.xsp/` is the WORKSPACE root if it contains a file named `workspace` (any content, even
  empty); otherwise it is a PROJECT root, namespaced `proj_<slug>` where <slug> is derived from
  the name of the directory that CONTAINS `.xsp/`.
  Walking up from a start path finds the NEAREST `.xsp/`, exactly like git finds `.git`: a
  project directory nested under a workspace resolves to the project, not the workspace.
"""
import pathlib
import re

MARKER_DIR = ".xsp"
WORKSPACE_MARKER_FILE = "workspace"
NAMESPACE_WORKSPACE = "workspace"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name):
    """Lowercase; runs of non [a-z0-9] become one underscore; strip leading/trailing underscores."""
    slug = _SLUG_RE.sub("_", name.lower()).strip("_")
    return slug or "root"


def find_xsp_root(start):
    """Walk up from `start` (a file or directory) to the filesystem root, returning the first
    directory that contains a `.xsp/` subdirectory, or None if none is found."""
    start = pathlib.Path(start).resolve()
    current = start if start.is_dir() else start.parent
    while True:
        candidate = current / MARKER_DIR
        if candidate.is_dir():
            return current
        if current.parent == current:  # reached the filesystem root
            return None
        current = current.parent


def resolve(start):
    """Resolve `start` to its xsp root and namespace.

    Returns a dict:
      found=True:  {"found": True, "root": "<abs path containing .xsp>", "namespace": "workspace" | "proj_<slug>"}
      found=False: {"found": False, "message": "no .xsp found"}
    """
    root = find_xsp_root(start)
    if root is None:
        return {"found": False, "message": "no .xsp found"}
    xsp_dir = root / MARKER_DIR
    is_workspace = (xsp_dir / WORKSPACE_MARKER_FILE).is_file()
    namespace = NAMESPACE_WORKSPACE if is_workspace else "proj_%s" % slugify(root.name)
    return {"found": True, "root": str(root), "namespace": namespace}
