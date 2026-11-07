"""`codeworld doctor`: verify the environment and (optionally) your LLM / embedding provider end to end."""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

from .config import get_settings

OK, BAD, WARN = "[ ok ]", "[FAIL]", "[warn]"


def run(args) -> int:
    s = get_settings()
    bad = 0

    def line(tag, msg):
        nonlocal bad
        bad += tag == BAD
        print(f"{tag} {msg}")
    print(f"CodeWorld doctor  (python {sys.version.split()[0]}, data dir {s.data_dir})")
    line(OK if sys.version_info >= (3, 10) else BAD, "Python >= 3.10")
    line(OK if shutil.which("git") else BAD, "git installed" + ("" if shutil.which("git") else " - install from https://git-scm.com"))
    try:
        sqlite3.connect(":memory:").execute("create virtual table t using fts5(x)")
        line(OK, f"SQLite FTS5 available (sqlite {sqlite3.sqlite_version})")
    except Exception:
        line(BAD, "SQLite FTS5 missing - use the python.org Python build")
    for mod in ("tree_sitter", "tree_sitter_python", "tree_sitter_javascript", "tree_sitter_typescript", "networkx", "numpy", "fastapi", "uvicorn", "httpx"):
        try:
            __import__(mod); line(OK, f"import {mod}")
        except Exception as e:
            line(BAD, f"import {mod}: {e} -> pip install -r backend/requirements.txt")
    try:
        import jedi  # noqa
        line(OK, "jedi available (resolver level 3)")
    except Exception:
        line(WARN, "jedi not installed - resolver level 3 disabled (optional)")
    try:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        (s.data_dir / ".w").write_text("x"); (s.data_dir / ".w").unlink()
        line(OK, "data dir writable")
    except Exception as e:
        line(BAD, f"data dir not writable: {e}")
    free = shutil.disk_usage(s.data_dir).free / 1e9
    line(OK if free > 2 else WARN, f"{free:.1f} GB free disk")
    line(OK if shutil.which("docker") else WARN, "docker " + ("found (optional)" if shutil.which("docker") else "not found (optional - you do not need it)"))
    line(OK if shutil.which("node") else WARN, "node " + ("found" if shutil.which("node") else "not found (only needed to rebuild the UI / VS Code extension)"))

    # ---- embeddings
    from .ai.embeddings import get_embedder
    t = time.time()
    emb = get_embedder(s)
    v = emb.embed_query("hash a password")
    line(OK, f"embeddings: {emb.name} dim={len(v)} ({time.time() - t:.1f}s)" + ("" if s.embed_backend == "hash" or emb.name.startswith(s.embed_backend) else f"  (requested '{s.embed_backend}' failed, fell back)"))

    # ---- LLM
    from .ai.llm import PRESETS, get_llm, LLMError
    llm = get_llm(s)
    if not llm.available:
        line(WARN, f"LLM: none configured (CODEWORLD_LLM_PROVIDER={s.llm_provider}) - offline mode. Presets: {', '.join(PRESETS)}, ollama, openai")
        return 1 if bad else 0
    print(f"       LLM chain: {llm.name}")
    for p in (llm.parts if hasattr(llm, "parts") else [llm]):
        if hasattr(p, "list_models") and getattr(args, "list_models", False):
            try:
                ms = p.list_models()
                print(f"       models for {p.name.split(':')[0]} ({len(ms)}): " + ", ".join(ms[:40]))
            except Exception as e:
                line(WARN, f"could not list models: {e}")
        t = time.time()
        try:
            out = p.chat([{"role": "user", "content": "Reply with exactly: pong"}], temperature=0, max_tokens=12)
            line(OK, f"{p.name}: reachable, replied {out.strip()[:30]!r} in {time.time() - t:.1f}s")
        except Exception as e:
            line(BAD, f"{p.name}: {e}")
            continue
        t = time.time()
        try:
            out = p.chat([{"role": "system", "content": 'Respond with ONE JSON object only, no prose.'},
                          {"role": "user", "content": 'Call the tool search_code with query "login". Format: {"tool": "<name>", "args": {...}}'}], temperature=0, max_tokens=80)
            from .agent.answer import _parse_json
            obj = _parse_json(out)
            if obj and obj.get("tool") == "search_code":
                line(OK, f"{p.name}: follows the JSON tool-call protocol ({time.time() - t:.1f}s) -> agent mode will work")
            else:
                line(WARN, f"{p.name}: did not return clean tool-call JSON ({out[:60]!r}); use 'agent tools' off, or a stronger model")
        except Exception as e:
            line(WARN, f"{p.name}: tool-call probe failed: {e}")
    print(f"       usage so far: {json.dumps(llm.usage())}")
    return 1 if bad else 0
