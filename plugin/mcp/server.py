# -*- coding: utf-8 -*-
"""server.py -- xsp plugin skeleton MCP server (stdio). One tool: `where`.

Style follows xscholar/xscholar/mcp/server.py (FastMCP, stdio, stdout reserved for the protocol)
but is deliberately thin: no adapter, no gates, no writes -- this ticket only proves the mechanism.

    python plugin/mcp/server.py               # serve over stdio
    python plugin/mcp/server.py --list-tools   # load, print tools to stderr, exit 0
"""
import contextlib
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "plugin"))
from resolve import resolve  # noqa: E402

INSTRUCTIONS = (
    "xsp plugin skeleton: one tool, `where`, resolves a directory to its xsp root and namespace "
    "using the same walk-up rule as the SessionStart hook. Stub only: no knowledge graph is attached.")


def build_server():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    server = FastMCP("xsp", instructions=INSTRUCTIONS)
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @server.tool(name="where", annotations=read, description=(
        "Resolve a path (default: this server process's cwd) to the nearest `.xsp/` directory and its "
        "namespace ('workspace' or 'proj_<slug>'), walking up like git finds `.git`. Returns "
        "{found: false, message: 'no .xsp found'} when none exists above the path."))
    def where(path: str | None = None) -> dict:
        return resolve(path or os.getcwd())

    return server


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with contextlib.redirect_stdout(sys.stderr):  # stdout is the protocol channel
        server = build_server()
    if "--list-tools" in argv:
        import asyncio
        for tool in asyncio.run(server.list_tools()):
            print("%-10s %s" % (tool.name, sorted((tool.inputSchema.get("properties") or {}).keys())), file=sys.stderr)
        return 0
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
