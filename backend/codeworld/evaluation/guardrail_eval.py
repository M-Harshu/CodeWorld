"""RQ4: how well does the PR guardrail detect seeded AI-style defects, and how noisy is it on real historical commits?"""
from __future__ import annotations
import difflib
import random
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from ..analysis import diffreview, health
from ..config import get_settings
from ..db import repo_db
from ..indexer import index_repo
from ..ingest import IGNORE_DIRS, RepoRef
from ..service import RepoContext


def _mod(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if parts and parts[0] in ("src", "lib"):
        parts = parts[1:]
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


def _diff(path: str, old: str, new: str) -> str:
    body = list(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{path}", f"b/{path}", n=0))
    return f"diff --git a/{path} b/{path}\n" + "".join(x if x.endswith("\n") else x + "\n" for x in body)


class Scratch:
    """A private copy of the repo that is edited + re-indexed for every mutation."""
    def __init__(self, ctx):
        self.s = get_settings()
        self.tmp = Path(tempfile.mkdtemp(prefix="cw_gr_"))
        self.src = self.tmp / "src"
        shutil.copytree(ctx.ref.src, self.src, ignore=shutil.ignore_patterns(*IGNORE_DIRS), symlinks=False)
        import sqlite3
        with sqlite3.connect(str(ctx.ref.db)) as a, sqlite3.connect(str(self.tmp / "index.db")) as b:
            a.backup(b)                                  # consistent copy even when the source is in WAL mode
        self.ref = RepoRef(f"gr-{self.tmp.name}", ctx.ref.name, self.tmp, self.src, self.tmp / "index.db")
        index_repo(self.s, self.ref)
        self.ctx = RepoContext(self.s, self.ref)

    def mutate(self, edits: dict[str, str]) -> str:
        """Apply {path: new_text}; return the unified diff; re-index."""
        diffs, self._orig = [], {}
        for p, new in edits.items():
            f = self.src / p
            old = f.read_text("utf8") if f.exists() else ""
            self._orig[p] = old
            diffs.append(_diff(p, old, new))
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(new, "utf8")
        index_repo(self.s, self.ref)
        return "\n".join(diffs)

    def restore(self):
        for p, old in self._orig.items():
            (self.src / p).write_text(old, "utf8")
        index_repo(self.s, self.ref)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _text(sc: Scratch, p: str) -> str:
    return (sc.src / p).read_text("utf8")


def seeded(ctx, per_type: int = 20, seed: int = 11) -> dict:
    rnd = random.Random(seed)
    sc = Scratch(ctx)
    g = sc.ctx.graph
    py = sorted(f["path"] for f in g.files() if f["path"].endswith(".py") and not f["props"].get("is_test") and f["props"].get("loc", 0) > 15)
    results: dict[str, list[dict]] = {k: [] for k in ("bad_member_import", "fake_package", "fake_attr_call", "duplicate_helper", "dangling_reference", "untested_change")}
    try:
        covered = health.tested_set(g)
        funcs = [n for n in g.nodes.values() if n["kind"] == "function" and not n["props"].get("is_test") and n["props"].get("loc", 0) >= 8
                 and n["props"].get("complexity", 1) >= 2 and not n["name"].startswith("__") and n["parent"] and n["parent"].startswith("file:")]
        name_count = Counter(n["name"] for n in g.nodes.values() if n["kind"] in ("function", "method", "class"))

        def run(kind, edits, expect_cat, token=None):
            diff = sc.mutate(edits)
            rep = diffreview.review_diff(sc.ctx, diff)
            hit = [f for f in rep["findings"] if f["category"] == expect_cat and (token is None or token in f["title"])]
            results[kind].append({"detected": bool(hit), "severity": hit[0]["severity"] if hit else None, "other_findings": len(rep["findings"]) - len(hit)})
            sc.restore()

        for i in range(per_type):
            a, b = rnd.sample(py, 2) if len(py) >= 2 else (py[0], py[0])
            ma = _mod(a)
            fake = f"cw_fake_{rnd.randrange(10**6)}"
            if ma and a != b:
                run("bad_member_import", {b: f"from {ma} import {fake}\n" + _text(sc, b)}, "hallucinated-import", fake)
                run("fake_attr_call", {b: _text(sc, b) + f"\n\nimport {ma} as _cw_mod\n\n\ndef _cw_probe():\n    return _cw_mod.{fake}()\n"}, "hallucinated-api", fake)
            run("fake_package", {b: f"import quantumjson_{fake}\n" + _text(sc, b)}, "hallucinated-import", f"quantumjson_{fake}")
            f = rnd.choice(funcs) if funcs else None
            if f:
                src = _text(sc, f["path"]).splitlines()
                body = "\n".join(src[f["start"] - 1:f["end"]])
                tgt = rnd.choice([p for p in py if p != f["path"]] or py)
                body2 = body.replace(f"def {f['name']}(", f"def {f['name']}_cw_copy(", 1)
                run("duplicate_helper", {tgt: _text(sc, tgt) + "\n\n\n" + body2 + "\n"}, "duplicate-logic")
                callers = [c for c in g.callers(f["id"], 1, 0.9) if g.file_of(c) != f["path"] and not g.props(c).get("is_test")]
                if callers and name_count[f["name"]] == 1:
                    new = "\n".join(src[:f["start"] - 1] + src[f["end"]:]) + "\n"
                    run("dangling_reference", {f["path"]: new}, "dangling-reference", f["name"])
                if f["id"] not in covered:
                    lines = src[:f["start"]]
                    # insert a trivial statement right after the signature line(s)
                    sig_end = f["start"] - 1
                    while sig_end < f["end"] and not src[sig_end].rstrip().endswith(":"):
                        sig_end += 1
                    indent = re.match(r"\s*", src[sig_end + 1]).group(0) if sig_end + 1 < len(src) else "    "
                    mod = src[:sig_end + 1] + [f"{indent}_cw_probe = 1"] + src[sig_end + 1:]
                    run("untested_change", {f["path"]: "\n".join(mod) + "\n"}, "untested-change")
    finally:
        sc.close()
    summary = {k: {"n": len(v), "detected": sum(x["detected"] for x in v), "recall": round(sum(x["detected"] for x in v) / len(v), 3) if v else None} for k, v in results.items()}
    return {"summary": summary, "rows": results}


import re  # noqa: E402


def real_commits(ctx, k: int = 25, max_files: int = 8) -> dict:
    """False-positive proxy: review the repo's own recent (human) commits against their parent snapshot."""
    src = ctx.ref.src
    if not (src / ".git").exists():
        return {"error": "needs a git clone with history"}
    out = subprocess.run(["git", "-C", str(src), "log", "-n", str(k * 4), "--no-merges", "--format=%H"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split()
    s = get_settings()
    rows, findings_all = [], []
    for sha in out:
        if len(rows) >= k:
            break
        files = subprocess.run(["git", "-C", str(src), "diff", "--name-only", f"{sha}~1", sha],
                               capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split()
        py = [f for f in files if f.endswith(".py")]
        if not py or len(py) > max_files:
            continue
        diff = subprocess.run(["git", "-C", str(src), "diff", "--unified=0", "--no-color", f"{sha}~1", sha, "--", *py],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        tmp = Path(tempfile.mkdtemp(prefix="cw_rc_"))
        try:
            dst = tmp / "src"
            dst.mkdir()
            tar = subprocess.run(["git", "-C", str(src), "archive", f"{sha}~1"], capture_output=True)
            if tar.returncode != 0:
                continue
            subprocess.run(["tar", "-x", "-C", str(dst)], input=tar.stdout, check=True)
            ref = RepoRef(f"rc-{sha[:8]}", ctx.ref.name, tmp, dst, tmp / "index.db")
            index_repo(s, ref)
            rep = diffreview.review_patch(RepoContext(s, ref), diff)
            rows.append({"sha": sha[:10], "files": len(py), "verdict": rep["verdict"], "counts": rep["counts"], "note": rep.get("note", "")[:60]})
            findings_all += [{**f, "sha": sha[:10]} for f in rep["findings"]]
        except Exception as e:
            rows.append({"sha": sha[:10], "error": str(e)[:120]})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    ok = [r for r in rows if "counts" in r]
    cat = Counter((f["category"], f["severity"]) for f in findings_all)
    return {"commits": len(ok), "with_high": sum(1 for r in ok if r["counts"]["high"]), "with_medium_or_high": sum(1 for r in ok if r["counts"]["high"] or r["counts"]["medium"]),
            "verdicts": dict(Counter(r["verdict"] for r in ok)), "by_category": {f"{c}/{s}": n for (c, s), n in cat.most_common()},
            "high_findings": [f for f in findings_all if f["severity"] == "high"][:40], "rows": rows}
