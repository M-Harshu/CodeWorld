"""Heuristic attack-surface map: which API endpoints can reach dangerous sinks, plus basic secret scanning.
This is a triage aid, NOT a replacement for a real SAST tool (CodeQL, Semgrep, Bandit)."""
from __future__ import annotations
import os
import re
from collections import deque
from pathlib import Path

from ..parser import ParseResult

SINKS = {
    "eval": ("critical", "Dynamic code execution (eval/exec)"),
    "process": ("high", "Process / shell execution"),
    "deserialize": ("high", "Unsafe deserialisation (pickle/yaml.load/...)"),
    "db": ("medium", "Database access - verify queries are parameterised"),
    "weak-crypto": ("medium", "Weak hash (md5/sha1)"),
    "fs": ("low", "Filesystem access"),
    "network": ("low", "Outbound network request"),
}
SEV = {"critical": 4, "high": 3, "medium": 2, "low": 1}
SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("Hard-coded credential", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
]
PLACEHOLDER = re.compile(r"(?i)example|changeme|placeholder|your[_-]|xxx|<.*>|\$\{|os\.environ|getenv|process\.env|test|dummy|fake|\*{3,}")


def call_flags(ctx) -> dict[tuple[str, int], list[str]]:
    out = {}
    with ctx.con() as con:
        for r in con.execute("SELECT json FROM parsed"):
            pr = ParseResult.from_json(r["json"])
            for c in pr.calls:
                if c.flags and any(f in ("dynamic_arg", "shell_true") for f in c.flags):
                    out[(pr.path, c.line)] = c.flags
    return out


def findings(ctx) -> list[dict]:
    g, flags = ctx.graph, call_flags(ctx)
    out = []
    for n in g.nodes.values():
        eff = n["props"].get("effects") or []
        if n["kind"] not in ("function", "method", "file") or n["props"].get("is_test"):
            continue
        for e in eff:
            if e not in SINKS:
                continue
            sev, msg = SINKS[e]
            line, extra = n["start"], ""
            for (p, ln), fl in flags.items():
                if p == n["path"] and n["start"] <= ln <= n["end"]:
                    if e == "process" and "shell_true" in fl:
                        sev, extra, line = "critical", " (shell=True)", ln
                    if e == "db" and "dynamic_arg" in fl:
                        sev, msg, line = "high", "Possible SQL injection: query built from dynamic string", ln
            out.append({"severity": sev, "kind": e, "message": msg + extra, "symbol": n["qualname"], "id": n["id"], "path": n["path"], "line": line})
    out.sort(key=lambda x: -SEV[x["severity"]])
    return out


def attack_surface(ctx, depth: int = 8, min_conf: float = 0.5) -> list[dict]:
    g, result = ctx.graph, []
    for api in [n for n in g.nodes.values() if n["kind"] == "api"]:
        handler = api["props"].get("handler")
        if not handler:
            continue
        prev, dq, seen = {handler: None}, deque([(handler, 0)]), {handler}
        risks = []
        while dq:
            cur, d = dq.popleft()
            for e in g.props(cur).get("effects") or []:
                if e in SINKS and not g.props(cur).get("is_test"):
                    path, x = [], cur
                    while x is not None:
                        path.append(g.nodes[x]["qualname"]); x = prev[x]
                    risks.append({"sink": e, "severity": SINKS[e][0], "symbol": g.nodes[cur]["qualname"], "path": g.nodes[cur]["path"],
                                  "line": g.nodes[cur]["start"], "route": path[::-1]})
            if d < depth:
                for dst, conf, *_ in g.out[cur].get("CALLS", []):
                    if conf >= min_conf and dst not in seen:
                        seen.add(dst); prev[dst] = cur; dq.append((dst, d + 1))
        if risks:
            risks.sort(key=lambda r: -SEV[r["severity"]])
            result.append({"api": api["name"], "id": api["id"], "handler": g.nodes[handler]["qualname"], "worst": risks[0]["severity"], "risks": risks[:12]})
    result.sort(key=lambda r: -SEV[r["worst"]])
    return result


def scan_secrets(ctx, limit: int = 40) -> list[dict]:
    out = []
    with ctx.con() as con:
        files = [r["path"] for r in con.execute("SELECT path FROM files WHERE language!='markdown'")]
    for path in files:
        try:
            text = ctx.abs_path(path).read_text("utf8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name, rx in SECRET_PATTERNS:
                m = rx.search(line)
                if m and not PLACEHOLDER.search(line):
                    out.append({"kind": name, "path": path, "line": i, "severity": "high", "note": "value redacted"})
        if len(out) >= limit:
            break
    return out[:limit]


_OSV_CACHE: dict[tuple[str, str], list[dict]] = {}


def scan_dependency_cves(ctx, max_queries: int = 15, timeout: float = 2.0) -> list[dict]:
    """Look up declared dependencies against the free OSV.dev database."""
    if os.environ.get("CODEWORLD_OFFLINE", "0") == "1":
        return []
    src = getattr(getattr(ctx, "ref", None), "src", None)
    if not src:
        return []
    src_path = Path(src)
    if not src_path.exists():
        return []

    from .importcheck import declared_dependencies
    deps = declared_dependencies(src_path)
    if not deps:
        return []

    is_js = (src_path / "package.json").exists()
    eco = "npm" if is_js else "PyPI"

    cves = []
    try:
        import httpx
        for pkg in sorted(deps)[:max_queries]:
            key = (eco, pkg.lower())
            if key in _OSV_CACHE:
                cves.extend(_OSV_CACHE[key])
                continue
            try:
                r = httpx.post(
                    "https://api.osv.dev/v1/query",
                    json={"package": {"name": pkg, "ecosystem": eco}},
                    timeout=timeout,
                )
                pkg_cves = []
                if r.status_code == 200:
                    data = r.json()
                    for v in data.get("vulns", [])[:2]:
                        pkg_cves.append({
                            "id": v.get("id"),
                            "package": pkg,
                            "ecosystem": eco,
                            "summary": v.get("summary", "Known vulnerability"),
                            "severity": "high" if "CRITICAL" in str(v.get("severity", [])) or "HIGH" in str(v.get("severity", [])) else "medium",
                            "reference": f"https://osv.dev/vulnerability/{v.get('id')}",
                        })
                _OSV_CACHE[key] = pkg_cves
                cves.extend(pkg_cves)
            except Exception:
                continue
    except Exception:
        pass
    return cves


def security_report(ctx) -> dict:
    def build():
        f, a, s = findings(ctx), attack_surface(ctx), scan_secrets(ctx)
        cves = scan_dependency_cves(ctx)
        return {"findings": f, "attack_surface": a, "secrets": s, "cves": cves,
                "summary": {"critical": sum(1 for x in f if x["severity"] == "critical"),
                            "high": sum(1 for x in f if x["severity"] == "high") + len(s) + sum(1 for c in cves if c["severity"] == "high"),
                            "medium": sum(1 for x in f if x["severity"] == "medium") + sum(1 for c in cves if c["severity"] == "medium"),
                            "endpoints_at_risk": len(a),
                            "cves_found": len(cves)},
                "disclaimer": "Heuristic triage based on call-graph reachability + OSV.dev CVE database; use CodeQL/Semgrep/Bandit for real security audits."}
    return ctx.cached("security", build)
