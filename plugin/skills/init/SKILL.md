---
name: init
description: Stub for xsp init. Use when the user runs /xsp:init or asks to initialize xsp for this folder.
---

stub -- prints the folder it would scope to.

Run `python "${CLAUDE_PLUGIN_ROOT}/resolve_cli.py"` (or, if that path is unavailable, resolve the
current working directory yourself using the same rule as `plugin/resolve.py`: walk up looking for
a `.xsp/` directory, nearest wins) and report the resolved root and namespace, or "no .xsp found".
This skill does no real initialization yet -- ticket 2026-09-17-product-plugin-skeleton is a proof
that skills/hooks/MCP load via `--plugin-dir`, not the real `/xsp init` UX (that is a later ticket).
