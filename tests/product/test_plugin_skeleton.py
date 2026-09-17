# -*- coding: utf-8 -*-
"""Tests for the xsp plugin skeleton (ticket 2026-09-17-product-plugin-skeleton).

Covers: walk-up resolution (nested subfolder -> nearest .xsp; nested workspace + project -> project
wins; none -> "no .xsp found"); the SessionStart hook script's JSON output shape; the MCP `where`
tool called in-process. No real knowledge-graph logic is under test -- this proves the mechanism.
"""
import importlib
import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugin"

sys.path.insert(0, str(PLUGIN_DIR))
resolve_mod = importlib.import_module("resolve")


# ---- walk-up resolution ---------------------------------------------------------------------

def test_nested_subfolder_finds_nearest_xsp(tmp_path):
    root = tmp_path / "proj"
    (root / ".xsp").mkdir(parents=True)
    nested = root / "a" / "b" / "c"
    nested.mkdir(parents=True)

    result = resolve_mod.resolve(nested)

    assert result == {"found": True, "root": str(root.resolve()), "namespace": "proj_proj"}


def test_nested_workspace_and_project_project_wins(tmp_path):
    workspace = tmp_path / "workspace_root"
    (workspace / ".xsp").mkdir(parents=True)
    (workspace / ".xsp" / "workspace").touch()

    project = workspace / "sub_project"
    (project / ".xsp").mkdir(parents=True)  # no "workspace" marker -> this .xsp is a project

    nested = project / "deep" / "nesting"
    nested.mkdir(parents=True)

    result = resolve_mod.resolve(nested)

    assert result["found"] is True
    assert result["root"] == str(project.resolve())
    assert result["namespace"] == "proj_sub_project"  # nearest .xsp wins, not the workspace above it


def test_workspace_root_resolves_to_workspace_namespace(tmp_path):
    workspace = tmp_path / "workspace_root"
    (workspace / ".xsp").mkdir(parents=True)
    (workspace / ".xsp" / "workspace").touch()

    result = resolve_mod.resolve(workspace)

    assert result == {"found": True, "root": str(workspace.resolve()), "namespace": "workspace"}


def test_none_found_when_no_xsp_anywhere_up(tmp_path):
    nested = tmp_path / "no_marker_here" / "deep"
    nested.mkdir(parents=True)

    result = resolve_mod.resolve(nested)

    assert result == {"found": False, "message": "no .xsp found"}


def test_slugify():
    assert resolve_mod.slugify("My Cool Project!!") == "my_cool_project"
    assert resolve_mod.slugify("___") == "root"


# ---- SessionStart hook script ---------------------------------------------------------------

def _run_hook(stdin_payload):
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_DIR / "hooks" / "session_start.py")],
        input=json.dumps(stdin_payload), capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_hook_output_shape_found(tmp_path):
    (tmp_path / ".xsp").mkdir()
    out = _run_hook({"hook_event_name": "SessionStart", "cwd": str(tmp_path)})

    assert "hookSpecificOutput" in out
    inner = out["hookSpecificOutput"]
    assert inner["hookEventName"] == "SessionStart"
    assert str(tmp_path.resolve()) in inner["additionalContext"]
    assert "proj_" in inner["additionalContext"]


def test_hook_output_shape_not_found(tmp_path):
    out = _run_hook({"hook_event_name": "SessionStart", "cwd": str(tmp_path)})

    inner = out["hookSpecificOutput"]
    assert inner["hookEventName"] == "SessionStart"
    assert "no .xsp found" in inner["additionalContext"]


def test_hook_falls_back_to_cwd_when_stdin_has_no_cwd(tmp_path, monkeypatch):
    (tmp_path / ".xsp").mkdir()
    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_DIR / "hooks" / "session_start.py")],
        input="not json", capture_output=True, text=True, timeout=15, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "proj_" in out["hookSpecificOutput"]["additionalContext"]


# ---- MCP `where` tool, in-process ------------------------------------------------------------

def _load_server_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("xsp_plugin_mcp_server", str(PLUGIN_DIR / "mcp" / "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_where_tool_in_process_found(tmp_path):
    module = _load_server_module()
    server = module.build_server()
    (tmp_path / ".xsp").mkdir()

    tools = _tool_map(server)
    result = tools["where"](path=str(tmp_path))

    assert result["found"] is True
    assert result["root"] == str(tmp_path.resolve())


def test_mcp_where_tool_in_process_not_found(tmp_path):
    module = _load_server_module()
    server = module.build_server()

    tools = _tool_map(server)
    result = tools["where"](path=str(tmp_path))

    assert result == {"found": False, "message": "no .xsp found"}


def _tool_map(server):
    """FastMCP stores registered tools in its ToolManager; call the underlying function directly
    so the test stays in-process (no stdio transport, no event loop)."""
    manager = server._tool_manager
    out = {}
    for name, tool in manager._tools.items():
        out[name] = tool.fn
    return out
