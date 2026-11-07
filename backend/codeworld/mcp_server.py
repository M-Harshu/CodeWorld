"""Minimal, dependency-free MCP (Model Context Protocol) server over stdio.

Lets Claude Code, Cursor, Copilot, Cline, Continue, ... query CodeWorld's code graph instead of grepping and reading whole files.
Run:  python -m codeworld.mcp_server --repo <repo-id | path-to-local-repo>
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

try:
    from . import __version__
    from .agent.tools import ToolError, ToolRegistry, compact
    from .config import get_settings
    from .indexer import index_repo
    from .ingest import register_repo
    from .service import get_context
except (ImportError, ValueError):
    _backend_dir = str(Path(__file__).resolve().parents[1])
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
    from codeworld import __version__
    from codeworld.agent.tools import ToolError, ToolRegistry, compact
    from codeworld.config import get_settings
    from codeworld.indexer import index_repo
    from codeworld.ingest import register_repo
    from codeworld.service import get_context

PROTOCOL = "2024-11-05"


def _resolve_repo(arg: str):
    s = get_settings()
    p = Path(arg).expanduser()
    if p.is_dir():
        from .db import registry_db
        p = p.resolve()
        with registry_db(s.data_dir) as c:
            row = c.execute("SELECT id FROM repos WHERE source=? AND kind='local'", (str(p),)).fetchone()
        ref = None
        if row:
            from .ingest import repo_ref
            ref = repo_ref(s, row["id"])
        if ref is None:
            ref = register_repo(s, p.name, str(p), "local")
        sys.stderr.write(f"[codeworld] indexing {p} ...\n")
        index_repo(s, ref)
        return get_context(ref.id, s)
    return get_context(arg, s)


def serve(arg: str):
    ctx = _resolve_repo(arg)
    if ctx is None:
        sys.stderr.write("repository not found\n")
        sys.exit(2)
    tools = ToolRegistry(ctx)

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                "serverInfo": {"name": "codeworld", "version": __version__},
                "instructions": "Use these tools to understand the repository's structure, call graph, tests and change impact before reading files."}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools.describe()}})
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                res = tools.call(p.get("name", ""), p.get("arguments") or {})
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": compact(res, 12000)}], "isError": False}})
            except ToolError as e:
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"internal error: {e}"}], "isError": True}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None and method and not method.startswith("notifications/"):
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})


def main():
    ap = argparse.ArgumentParser(description="CodeWorld MCP server (stdio)")
    ap.add_argument("--repo", required=True, help="repo id from the registry, or a local directory to index and serve")
    serve(ap.parse_args().repo)


if __name__ == "__main__":
    main()
