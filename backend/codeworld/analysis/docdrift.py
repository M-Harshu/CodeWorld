"""Doc-drift detector: finds README / docstrings that mention removed, renamed, or non-existent symbols."""
from __future__ import annotations
import re
from pathlib import Path


_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_\.]{3,})`")
_COMMON_IGNORES = frozenset({
    "true", "false", "none", "null", "self", "cls", "args", "kwargs", "main",
    "init", "dict", "list", "tuple", "str", "int", "bool", "float", "path",
    "json", "yaml", "toml", "http", "https", "localhost", "utf-8", "utf8",
    "python", "node", "npm", "pytest", "docker", "git", "github", "setup",
    "test", "tests", "bash", "powershell", "repo", "head", "base", "origin"
})


def check_doc_drift(ctx) -> list[dict]:
    """Check README files and markdown documentation for references to non-existent symbols."""
    g = ctx.graph
    defined_symbols: set[str] = set()
    for n in g.nodes.values():
        q = n.get("qualname")
        if q:
            defined_symbols.add(q)
            defined_symbols.add(q.split(".")[-1])
            defined_symbols.add(q.split("::")[-1])

    findings: list[dict] = []
    src = getattr(getattr(ctx, "ref", None), "src", None)
    if not src:
        return findings

    src_path = Path(src)
    if not src_path.exists():
        return findings

    md_files = list(src_path.glob("*.md")) + list((src_path / "docs").glob("*.md")) if (src_path / "docs").exists() else list(src_path.glob("*.md"))

    for p in md_files[:10]:
        try:
            rel = p.relative_to(src_path).as_posix()
            text = p.read_text("utf-8", errors="replace")
        except Exception:
            continue

        for line_num, line in enumerate(text.splitlines(), 1):
            for m in _IDENT.finditer(line):
                tok = m.group(1)
                clean_tok = tok.strip("()").split("(")[0]
                if "." in clean_tok and not clean_tok.endswith((".py", ".js", ".ts", ".md", ".json")):
                    base_name = clean_tok.split(".")[-1]
                else:
                    base_name = clean_tok

                if base_name.lower() in _COMMON_IGNORES or len(base_name) < 4:
                    continue
                if "/" in clean_tok or "\\" in clean_tok or "--" in clean_tok:
                    continue
                # Looks like a function/method/class identifier
                if ("_" in base_name or any(c.isupper() for c in base_name[1:])) and not base_name.isupper():
                    if clean_tok not in defined_symbols and base_name not in defined_symbols:
                        findings.append({
                            "path": rel,
                            "line": line_num,
                            "symbol": clean_tok,
                            "category": "doc-drift",
                            "severity": "low",
                            "title": f"Documented symbol '{clean_tok}' not found in codebase",
                            "detail": f"`{rel}:{line_num}` references `{clean_tok}`, which does not match any indexed class, function, or method. It may have been removed or renamed."
                        })

    return findings[:30]

