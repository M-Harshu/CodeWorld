"""Detect hallucinated imports / APIs: names that AI assistants invent (missing packages, missing members, missing files)."""
from __future__ import annotations
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ALIASES = {"pil": "pillow", "cv2": "opencv-python", "yaml": "pyyaml", "sklearn": "scikit-learn", "bs4": "beautifulsoup4",
           "dateutil": "python-dateutil", "jwt": "pyjwt", "dotenv": "python-dotenv", "skimage": "scikit-image", "attr": "attrs",
           "google": "protobuf", "serial": "pyserial", "magic": "python-magic", "git": "gitpython", "mysqldb": "mysqlclient",
           "psycopg2": "psycopg2-binary", "docx": "python-docx", "pptx": "python-pptx", "fitz": "pymupdf", "tree_sitter": "tree-sitter"}
NODE_BUILTINS = set("""assert buffer child_process cluster crypto dgram dns events fs http http2 https net os path perf_hooks process punycode
querystring readline repl stream string_decoder timers tls tty url util v8 vm worker_threads zlib module console""".split())


def _norm(n: str) -> str:
    return re.split(r"[<>=!~;\[ ]", n.strip(), maxsplit=1)[0].lower().replace("-", "_").replace(".", "_")


def declared_dependencies(src: Path) -> set[str]:
    deps: set[str] = set()
    for p in list(src.glob("requirements*.txt")) + list(src.glob("**/requirements*.txt"))[:5]:
        for line in p.read_text("utf8", errors="replace").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith(("-", "git+", "http")):
                deps.add(_norm(line))
    pp = src / "pyproject.toml"
    if pp.exists():
        try:
            import tomllib
            d = tomllib.loads(pp.read_text("utf8"))
            for x in d.get("project", {}).get("dependencies", []):
                deps.add(_norm(x))
            for grp in d.get("project", {}).get("optional-dependencies", {}).values():
                deps.update(_norm(x) for x in grp)
            deps.update(_norm(k) for k in d.get("tool", {}).get("poetry", {}).get("dependencies", {}))
        except Exception:
            pass
    sp = src / "setup.py"
    if sp.exists():
        deps.update(_norm(x) for x in re.findall(r"[\"']([A-Za-z0-9_.\-]+)(?:[<>=!~][^\"']*)?[\"']", sp.read_text("utf8", errors="replace")))
    pj = src / "package.json"
    if pj.exists():
        try:
            d = json.loads(pj.read_text("utf8"))
            for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                deps.update(d.get(k, {}).keys())
        except Exception:
            pass
    return deps


def _py_declared(top: str, deps: set[str]) -> bool:
    t = _norm(top)
    return t in deps or ALIASES.get(t, "").replace("-", "_") in deps or any(d.replace("_", "") == t.replace("_", "") for d in deps)



def _get_all_paths(ctx) -> set[str]:
    """Retrieve all indexed file paths from the repository context."""
    if hasattr(ctx, "con"):
        try:
            with ctx.con() as con:
                return {r["path"] for r in con.execute("SELECT path FROM files")}
        except Exception:
            pass
    return set()


_PACKAGE_REGISTRY_CACHE: dict[tuple[str, str], bool] = {}


def check_package_registry_exists(pkg: str, ecosystem: str = "pypi", timeout: float = 1.5) -> bool | None:
    """Check PyPI or npm registry to confirm if a package genuinely exists.
    Returns:
        True: Package exists on public registry.
        False: Package does NOT exist (404, hallucinated package name).
        None: Network disabled, offline, or request timed out.
    """
    if not pkg:
        return None
    key = (ecosystem, pkg.lower())
    if key in _PACKAGE_REGISTRY_CACHE:
        return _PACKAGE_REGISTRY_CACHE[key]
    if os.environ.get("CODEWORLD_OFFLINE", "0") == "1" or os.environ.get("CODEWORLD_CHECK_REGISTRY", "1") == "0":
        return None
    try:
        import httpx
        url = f"https://pypi.org/pypi/{pkg}/json" if ecosystem == "pypi" else f"https://registry.npmjs.org/{pkg}"
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        exists = (r.status_code == 200)
        _PACKAGE_REGISTRY_CACHE[key] = exists
        return exists
    except Exception:
        return None


def check_diff_imports(ctx, files: dict) -> list[dict]:
    """Check imports that appear in the diff's added lines directly (no git apply needed).

    Called alongside check_imports so that even when git apply partially fails
    on Windows the hallucinated-import / hallucinated-api checks still fire.
    """
    g = ctx.graph
    src_dir = getattr(getattr(ctx, "ref", None), "src", None)
    deps = declared_dependencies(Path(src_dir)) if src_dir and Path(src_dir).exists() else set()
    std = set(getattr(sys, "stdlib_module_names", set()))
    out: list[dict] = []
    all_paths: set[str] = set(_get_all_paths(ctx))
    parsed_db = {}
    if hasattr(ctx, "con"):
        try:
            with ctx.con() as con:
                from ..parser import ParseResult
                parsed_db = {r["path"]: ParseResult.from_json(r["json"]) for r in con.execute("SELECT path, json FROM parsed")}
                if not all_paths:
                    all_paths = set(parsed_db.keys())
        except Exception:
            pass

    py_plain_import = re.compile(r"^\s*import\s+(\S+)")
    py_from_import  = re.compile(r"^\s*from\s+(\S+)\s+import\s+(.+)")

    for path, finfo in files.items():
        if finfo.get("deleted") or not path.endswith(".py"):
            continue
        added_text: list[str] = finfo.get("added_text", [])
        added_sorted = sorted(finfo.get("added", set()))
        for idx, line in enumerate(added_text):
            ln = added_sorted[idx] if idx < len(added_sorted) else 0
            # plain: import foo
            m = py_plain_import.match(line)
            if m:
                top = m.group(1).split(".")[0]
                if top and top not in std:
                    internal = any(
                        p == top + ".py" or p.startswith(top + "/") or
                        p == f"src/{top}.py" or p.startswith(f"src/{top}/")
                        for p in all_paths
                    )
                    installed = _safe_spec(top) and importlib.util.find_spec(top) is not None
                    if not internal and not installed and not _py_declared(top, deps):
                        pypi_ok = check_package_registry_exists(top, "pypi")
                        pypi_note = " Confirmed not found on PyPI (likely hallucinated)." if pypi_ok is False else ""
                        out.append({"severity": "medium", "category": "hallucinated-import", "path": path, "line": ln,
                                    "title": f"Unresolvable package '{top}'",
                                    "detail": f"`{top}` is not part of the repo, the standard library, an installed package, or declared dependencies.{pypi_note}"})
                continue
            # from X import Y [as Z], ...
            m2 = py_from_import.match(line)
            if not m2:
                continue
            raw_mod = m2.group(1)
            level = len(raw_mod) - len(raw_mod.lstrip("."))
            mod = raw_mod.lstrip(".")
            names_str = m2.group(2)
            names = [n.strip().split(" as ")[0].strip() for n in re.split(r",\s*", names_str) if n.strip() and n.strip() != "*"]
            if level > 0:
                continue   # relative imports: leave to the existing check_imports
            if not mod or not names:
                continue
            parts = mod.split(".")
            target = None
            for cand in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py",
                         "src/" + "/".join(parts) + ".py", "src/" + "/".join(parts) + "/__init__.py"):
                if cand in all_paths:
                    target = cand
                    break
            if target:
                tnode = g.node(f"file:{target}")
                exports: set[str] = set(tnode["props"].get("exports", [])) if tnode else set()
                tpr = parsed_db.get(target)
                if tpr:
                    exports |= {s.name for s in tpr.symbols}
                for name in names:
                    if name not in exports:
                        sub = "/".join(parts) + "/" + name + ".py"
                        if sub not in all_paths:
                            out.append({"severity": "high", "category": "hallucinated-import", "path": path, "line": ln,
                                        "title": f"`{name}` does not exist in `{mod}`",
                                        "detail": f"`{target}` defines no top-level name `{name}`. Likely a hallucinated or renamed API."})
            else:
                top = parts[0]
                if top and top not in std:
                    installed = _safe_spec(top) and importlib.util.find_spec(top) is not None
                    if not installed and not _py_declared(top, deps):
                        out.append({"severity": "medium", "category": "hallucinated-import", "path": path, "line": ln,
                                    "title": f"Unresolvable package '{top}'",
                                    "detail": f"`{top}` is not part of the repo, the standard library, or the declared dependencies."})
    return out


def check_imports(ctx, added: dict[str, set[int]] | None = None) -> list[dict]:
    """Check imports (optionally only those on `added` line numbers per file). Returns findings."""
    from ..parser import ParseResult
    g, out = ctx.graph, []
    deps = declared_dependencies(ctx.ref.src)
    std = set(getattr(sys, "stdlib_module_names", set()))
    with ctx.con() as con:
        parsed = {r["path"]: ParseResult.from_json(r["json"]) for r in con.execute("SELECT path, json FROM parsed")}
    for path, pr in parsed.items():
        if added is not None and path not in added:
            continue
        fnode = g.node(f"file:{path}")
        scope = fnode["props"].get("scope", {}) if fnode else {}
        want = added.get(path) if added else None
        for imp in pr.imports:
            if want is not None and imp.line not in want:
                continue
            if pr.language == "python":
                top = imp.module.split(".")[0] if imp.module else ""
                if imp.level == 0 and top and top not in std:
                    internal = bool(g.node(f"file:{top}.py")) or any(p.startswith(top + "/") or p == top + ".py" or p.startswith("src/" + top + "/") for p in parsed)
                    installed = _safe_spec(top) and importlib.util.find_spec(top) is not None
                    if not internal and not installed and not _py_declared(top, deps):
                        pypi_ok = check_package_registry_exists(top, "pypi")
                        pypi_note = " Confirmed not found on PyPI (likely hallucinated)." if pypi_ok is False else ""
                        out.append({"severity": "medium", "category": "hallucinated-import", "path": path, "line": imp.line,
                                    "title": f"Unresolvable package '{top}'",
                                    "detail": f"`{top}` is not part of the repo, the standard library, an installed package, or the declared dependencies.{pypi_note} AI assistants sometimes invent package names - verify it exists before installing."})
                if imp.kind == "from" and imp.names:
                    target = _internal_target(g, parsed, path, imp)
                    if target:
                        tnode = g.node(f"file:{target}")
                        exports = set(tnode["props"].get("exports", [])) | set(tnode["props"].get("scope", {}).keys()) if tnode else set()
                        for name, alias in imp.names:
                            if name == "*" or name in exports:
                                continue
                            sub = target.rsplit("/", 1)[0] + "/" + name + ".py" if target.endswith("__init__.py") else None
                            if sub and sub in parsed:
                                continue
                            out.append({"severity": "high", "category": "hallucinated-import", "path": path, "line": imp.line,
                                        "title": f"`{name}` does not exist in `{imp.module or '.'}`",
                                        "detail": f"`{target}` defines no top-level name `{name}`. Likely a hallucinated or renamed API."})
            else:
                if imp.module.startswith("."):
                    if not any(t[0] in ("symbol", "module") for k, t in scope.items() if k in [(n[1] or n[0]) for n in imp.names] + [imp.alias or ""]) and not _js_resolves(parsed, path, imp.module):
                        out.append({"severity": "high", "category": "hallucinated-import", "path": path, "line": imp.line,
                                    "title": f"Relative import '{imp.module}' does not resolve", "detail": "No such file in the repository."})
                else:
                    base = imp.module.split("/")[0] if not imp.module.startswith("@") else "/".join(imp.module.split("/")[:2])
                    if base.replace("node:", "") not in NODE_BUILTINS and base not in deps:
                        npm_ok = check_package_registry_exists(base, "npm")
                        npm_note = " Confirmed not found on npm registry." if npm_ok is False else ""
                        out.append({"severity": "medium", "category": "hallucinated-import", "path": path, "line": imp.line,
                                    "title": f"Package '{base}' is not in package.json",
                                    "detail": f"Not declared as a dependency - verify it exists.{npm_note}"})
        # attribute-level: alias.func() where alias is an internal module that lacks `func`
        for c in pr.calls:
            if want is not None and c.line not in want:
                continue
            parts = c.text.split(".")
            if len(parts) == 2 and parts[0] in scope and scope[parts[0]][0] == "module":
                tgt = g.node(f"file:{scope[parts[0]][1]}")
                if tgt:
                    ex = set(tgt["props"].get("exports", [])) | set(tgt["props"].get("scope", {}).keys())
                    if parts[1] not in ex and not parts[1].startswith("__"):
                        out.append({"severity": "high", "category": "hallucinated-api", "path": path, "line": c.line,
                                    "title": f"`{c.text}()` - `{parts[1]}` is not defined in `{scope[parts[0]][1]}`",
                                    "detail": "The module exists but has no such function/class/variable."})
    return out


def _safe_spec(top: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_]\w*", top))


def _internal_target(g, parsed, path, imp) -> str | None:
    mod = imp.module
    if imp.level:
        base = path.split("/")[:-1]
        base = base[: len(base) - (imp.level - 1)] if imp.level - 1 <= len(base) else []
        parts = base + (mod.split(".") if mod else [])
    else:
        parts = mod.split(".") if mod else []
    for cand in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py", "src/" + "/".join(parts) + ".py", "src/" + "/".join(parts) + "/__init__.py"):
        if cand in parsed:
            return cand
    return None


def _js_resolves(parsed, path, mod) -> bool:
    import posixpath
    base = posixpath.normpath(posixpath.join(posixpath.dirname(path), mod))
    for ext in ("", ".js", ".jsx", ".mjs", ".ts", ".tsx", "/index.js", "/index.ts", "/index.tsx"):
        if base + ext in parsed:
            return True
    return False
