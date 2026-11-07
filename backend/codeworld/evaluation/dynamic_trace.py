"""Generate call-graph ground truth by tracing a real run (tests or a script) with sys.setprofile.

WARNING: this EXECUTES the target repository's code. Only run it on code you trust, ideally inside a container/VM.
    python -m codeworld.evaluation.dynamic_trace --root path/to/repo --run "pytest -q tests" --out truth.json
"""
from __future__ import annotations
import argparse
import json
import os
import runpy
import shlex
import sys
from pathlib import Path


def trace(root: Path, run: str) -> list[list[str]]:
    root = root.resolve()
    edges: set[tuple[str, str]] = set()
    cache: dict[str, str | None] = {}

    def rel(fn: str) -> str | None:
        if fn not in cache:
            if fn.startswith("<"):                       # <frozen os>, <string>, ... resolve under cwd otherwise
                cache[fn] = None
                return None
            try:
                p = Path(fn).resolve()
                cache[fn] = p.relative_to(root).as_posix() if root in p.parents and "site-packages" not in p.parts else None
            except Exception:
                cache[fn] = None
        return cache[fn]

    def qualname(frame):
        """co_qualname exists only on Python >= 3.11; on 3.10 recover Class.method from the frame's first argument."""
        code = frame.f_code
        q = getattr(code, "co_qualname", None)
        if q:
            return q
        name = code.co_name
        if code.co_argcount and code.co_varnames[0] in ("self", "cls"):
            obj = frame.f_locals.get(code.co_varnames[0])
            klass = obj if isinstance(obj, type) else type(obj)
            for k in getattr(klass, "__mro__", ()):
                fn = k.__dict__.get(name)
                fn = getattr(fn, "__func__", fn)
                if getattr(fn, "__code__", None) is code:
                    return f"{k.__qualname__}.{name}"
        return name

    def key(frame):
        code = frame.f_code
        r = rel(code.co_filename)
        q = qualname(frame).replace("<locals>.", "")
        return None if r is None or q.startswith("<") else f"{r}::{q}"

    def prof(frame, event, arg):
        if event != "call":
            return
        callee = key(frame)
        if not callee:
            return
        f = frame.f_back
        while f is not None:
            caller = key(f)
            if caller:
                if caller != callee:
                    edges.add((caller, callee))
                return
            if rel(f.f_code.co_filename) is not None and f.f_code.co_name == "<module>":
                return
            f = f.f_back

    sys.path.insert(0, str(root))
    os.chdir(root)
    argv = shlex.split(run)
    sys.setprofile(prof)
    try:
        if argv[0] == "pytest":
            import pytest
            pytest.main(argv[1:] + ["-p", "no:cacheprovider"])
        else:
            sys.argv = argv
            runpy.run_path(argv[0], run_name="__main__")
    finally:
        sys.setprofile(None)
    return sorted([list(e) for e in edges])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True); ap.add_argument("--run", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    edges = trace(Path(a.root), a.run)
    Path(a.out).write_text(json.dumps(edges, indent=1))
    print(f"wrote {len(edges)} observed call edges to {a.out}")


if __name__ == "__main__":
    main()
