"""Benchmark and evaluation suite for CodeWorld.

    python -m codeworld.evaluation.suite --repos click,flask --steps all --out results/
Steps: scale, doc2code, callgraph, callers, impact, guardrail, commits       (use --steps scale,doc2code to pick)
Everything is written as JSON per repository plus summary.csv / report.md (+ PNG figures if matplotlib is installed).
LLM-dependent comparisons are NOT run here; run `codeworld eval qa --modes ...agentic` with your provider configured.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import platform
try:
    import resource as _res
except ImportError:
    _res = None
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def sh(cmd, cwd=None, timeout=1800, **kw):
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    env = kw.get("env") or os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    kw["env"] = env
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, **kw)


def rss_mb() -> int:
    try:
        if _res is None:
            # Windows: use psutil if available, else estimate from process
            try:
                import psutil
                import os as _os
                return psutil.Process(_os.getpid()).memory_info().rss // (1024 * 1024)
            except Exception:
                return 0
        v = _res.getrusage(_res.RUSAGE_SELF).ru_maxrss
        return int(v / (1024 * 1024) if sys.platform == "darwin" else v / 1024)
    except Exception:
        return 0


def prepare(cfg: dict, work: Path, depth: int, install: bool) -> Path:
    dst = work / cfg["name"]
    if not dst.exists():
        r = sh(["git", "clone", "--depth", str(depth), cfg["url"], str(dst)])
        if r.returncode:
            raise RuntimeError(r.stderr[-300:])
    if install and cfg.get("install"):
        r = sh([sys.executable, "-m", "pip", "install", "-q", "pytest", *cfg["install"]], cwd=dst)
        if r.returncode:
            print("   pip install warning:", r.stderr[-200:])
    return dst


def run_repo(cfg, args, out: Path) -> dict:
    from ..config import get_settings
    from ..db import registry_db
    from ..indexer import index_repo
    from ..ingest import register_repo
    from ..service import get_context
    from . import autoqa, callgraph_eval, guardrail_eval, impact_eval, qa_eval
    from .dynamic_trace import trace

    steps = set(args.steps.split(",")) if args.steps != "all" else {"scale", "doc2code", "callgraph", "callers", "impact", "guardrail", "commits"}
    res: dict = {"repo": cfg["name"], "steps": sorted(steps)}
    src = prepare(cfg, Path(args.work), args.depth, install=bool({"callgraph", "callers"} & steps))
    sha = sh(["git", "-C", str(src), "rev-parse", "HEAD"]).stdout.strip()
    res["commit"] = sha
    s = get_settings()
    ref = register_repo(s, cfg["name"], str(src.resolve()), "local")
    t0 = time.time()
    stats = index_repo(s, ref)
    ctx = get_context(ref.id, s)
    res["scale"] = {"files": stats["files"], "loc": stats["loc"], "symbols": stats["classes"] + stats["functions"] + stats["methods"], "call_edges": stats["edges"].get("CALLS", 0),
                    "index_seconds": round(time.time() - t0, 2), "peak_rss_mb": rss_mb(), "git": stats["git"]}
    print(f"   indexed {stats['files']} files / {stats['loc']:,} LOC in {res['scale']['index_seconds']}s")
    truth = None
    if {"callgraph", "callers"} & steps:
        tf = out / f"{cfg['name']}_truth.json"
        if not tf.exists():
            print("   tracing tests (executes the repo's code) ...")
            pp = str(Path(__file__).resolve().parents[2]) + os.pathsep + os.environ.get("PYTHONPATH", "")
            existing = [f for f in cfg.get("trace_files", []) if (src / f).exists()] or ["tests"]
            run_cmd = "pytest -q -p no:cacheprovider --maxfail=50 " + " ".join(existing)
            r = sh([sys.executable, "-m", "codeworld.evaluation.dynamic_trace", "--root", str(src), "--run", run_cmd, "--out", str(tf)],
                   timeout=args.trace_timeout, env={**os.environ, "PYTHONPATH": pp})
            if r.returncode:
                res["trace_error"] = (r.stdout + r.stderr)[-400:]
        if tf.exists():
            truth = json.loads(tf.read_text())
            res["trace_edges"] = len(truth)
    if "doc2code" in steps:
        ds = autoqa.doc2code(ctx, args.n)
        r = qa_eval.evaluate(ctx, ds, ["bm25", "vector", "hybrid", "graph", "auto"], 10)
        res["doc2code"] = {"n": len(ds), **r["per_mode"], "rows": r.get("rows")}
        print("   doc2code", {m: v.get("sym_R@10") for m, v in r["per_mode"].items()})
    if "callgraph" in steps and truth:
        res["callgraph"] = callgraph_eval.evaluate(ctx, str(out / f"{cfg['name']}_truth.json"))
        print("   callgraph recall(all/visible)", {k: (v["recall"], v["visible"]["recall"]) for k, v in res["callgraph"]["levels"].items()})
    if "callers" in steps and truth:
        ds = autoqa.callers(ctx, truth, min(args.n, 100))
        r = qa_eval.evaluate(ctx, ds, ["bm25", "vector", "hybrid", "graph", "auto"], 10)
        # direct graph lookup (what an agent's find_callers tool returns) vs dynamic truth
        g = ctx.graph
        p_, r_ = [], []
        for q in ds:
            got = {g.nodes[c]["qualname"] for c in g.callers(q["target"], 1, 0.5) if c in g.nodes}
            gold = set(q["gold_symbols"])
            p_.append(len(got & gold) / len(got) if got else 0.0)
            r_.append(len(got & gold) / len(gold))
        res["callers"] = {"n": len(ds), **r["per_mode"], "rows": r.get("rows"), "tool_find_callers": {"precision_lower_bound": round(sum(p_) / max(1, len(p_)), 3), "recall": round(sum(r_) / max(1, len(r_)), 3)}}
        print("   callers", res["callers"]["tool_find_callers"])
    if "impact" in steps and stats["git"]:
        res["impact"] = impact_eval.evaluate(ctx, args.commits)
        if "methods" in res["impact"]:
            print("   impact P@5", {k: v["precision@5"] for k, v in res["impact"]["methods"].items()})
    if "guardrail" in steps:
        res["guardrail"] = guardrail_eval.seeded(ctx, args.per_type)
        print("   guardrail recall", {k: v["recall"] for k, v in res["guardrail"]["summary"].items()})
    if "commits" in steps and stats["git"]:
        res["real_commits"] = guardrail_eval.real_commits(ctx, args.commit_k)
        rc = res["real_commits"]
        print("   real commits:", {k: rc.get(k) for k in ("commits", "with_high", "with_medium_or_high")})
    return res


def bootstrap_ci(values: list[float], n_boot: int = 1000, alpha: float = 0.05, seed: int = 42) -> tuple[float, float]:
    """95% bootstrap confidence interval over sample values."""
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return round(values[0], 4), round(values[0], 4)
    import random
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        sample = [values[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    low_idx = int((alpha / 2.0) * n_boot)
    high_idx = int((1.0 - alpha / 2.0) * n_boot)
    return round(means[low_idx], 4), round(means[high_idx], 4)


def wilcoxon_signed_rank(x: list[float], y: list[float]) -> tuple[float, float]:
    """Pure-Python paired Wilcoxon signed-rank test. Returns (statistic, p-value approx)."""
    import math
    diffs = [a - b for a, b in zip(x, y) if a != b]
    n = len(diffs)
    if n == 0:
        return 0.0, 1.0
    abs_diffs = sorted([(abs(d), d > 0) for d in diffs])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and abs_diffs[j][0] == abs_diffs[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    w_pos = sum(ranks[k] for k in range(n) if abs_diffs[k][1])
    w_neg = sum(ranks[k] for k in range(n) if not abs_diffs[k][1])
    w = min(w_pos, w_neg)
    mean_w = n * (n + 1) / 4.0
    var_w = n * (n + 1) * (2 * n + 1) / 24.0
    z = (w - mean_w) / math.sqrt(var_w) if var_w > 0 else 0.0
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))
    return round(w, 2), round(min(1.0, max(0.0, p)), 4)


def holm_bonferroni(p_values: list[float]) -> list[float]:
    """Holm-Bonferroni step-down correction for multiple testing."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    running_max = 0.0
    for rank, (orig_idx, p) in enumerate(indexed):
        adj = min(1.0, (m - rank) * p)
        running_max = max(running_max, adj)
        adjusted[orig_idx] = round(running_max, 4)
    return adjusted


def aggregate(results: list[dict], out: Path) -> None:
    rows = []
    for r in results:
        base = {"repo": r["repo"], "commit": r["commit"][:8]}
        sc = r.get("scale", {})
        rows.append({**base, "experiment": "scale", "metric": "index_seconds", "value": sc.get("index_seconds"), "detail": f"{sc.get('loc')} LOC"})
        for key in ("doc2code", "callers"):
            for mode, m in r.get(key, {}).items():
                if isinstance(m, dict) and mode != "tool_find_callers":
                    for k, v in m.items():
                        rows.append({**base, "experiment": key, "metric": f"{mode}:{k}", "value": v})
        for lv, m in r.get("callgraph", {}).get("levels", {}).items():
            for k in ("precision", "recall", "f1", "precision_executed_callers"):
                rows.append({**base, "experiment": "callgraph", "metric": f"{lv}:{k}", "value": m[k]})
            for k in ("precision_executed_callers", "recall", "f1"):
                rows.append({**base, "experiment": "callgraph_visible", "metric": f"{lv}:{k}", "value": m["visible"][k]})
        for name, m in r.get("impact", {}).get("methods", {}).items():
            for k, v in m.items():
                rows.append({**base, "experiment": "impact", "metric": f"{name}:{k}", "value": v})
        for k, m in r.get("guardrail", {}).get("summary", {}).items():
            rows.append({**base, "experiment": "guardrail_seeded", "metric": k, "value": m["recall"], "detail": f"{m['detected']}/{m['n']}"})
        rc = r.get("real_commits", {})
        if "commits" in rc:
            rows.append({**base, "experiment": "guardrail_real", "metric": "commits_with_high", "value": rc["with_high"], "detail": f"of {rc['commits']}"})
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "commit", "experiment", "metric", "value", "detail"])
        w.writeheader()
        w.writerows(rows)
    L = ["# Experiment report", f"_generated {time.strftime('%Y-%m-%d %H:%M')} on {platform.platform()} / Python {platform.python_version()}; embedder = {os.environ.get('CODEWORLD_EMBED_BACKEND', 'hash')}_", ""]
    L += ["## Repositories", "| repo | commit | files | LOC | call edges | index s | peak RSS MB |", "|---|---|---|---|---|---|---|"]
    for r in results:
        s = r["scale"]
        L.append(f"| {r['repo']} | {r['commit'][:8]} | {s['files']} | {s['loc']:,} | {s['call_edges']:,} | {s['index_seconds']} | {s['peak_rss_mb']} |")

    def table(title, key, metrics, modes=("bm25", "vector", "hybrid", "graph", "auto")):
        have = [r for r in results if key in r]
        if not have:
            return
        L.extend(["", f"## {title}", "| repo | n | " + " | ".join(f"{m}" for m in modes) + " |", "|---|---|" + "---|" * len(modes)])
        for r in have:
            L.append(f"| {r['repo']} | {r[key]['n']} | " + " | ".join(str(r[key].get(m, {}).get(metrics, "-")) for m in modes) + " |")
        if len(have) > 1:
            L.append("| **mean** | | " + " | ".join(f"**{round(sum(r[key][m].get(metrics, 0) for r in have) / len(have), 3)}**" for m in modes) + " |")
    table("Doc-to-code retrieval, symbol Recall@10 (auto-generated queries)", "doc2code", "sym_R@10")
    table("Doc-to-code retrieval, symbol MRR", "doc2code", "sym_mrr")
    table("Doc-to-code retrieval, symbol Recall@1", "doc2code", "sym_R@1")
    table("Caller questions, symbol Recall@10 (gold = callers seen in a dynamic trace)", "callers", "sym_R@10")
    cg = [r for r in results if "callgraph" in r]
    if cg:
        L += ["", "## Call-graph fidelity vs dynamic trace by resolver level", "Recall is exact w.r.t. observed calls; precision is only a lower bound (unexecuted edges count as wrong). `P_exec` = precision restricted to callers that actually ran.", "",
              "`visible` = the subset of observed calls that have a matching call expression in the caller's source (excludes decorators, properties, dunder protocols, framework callbacks).", "",
              "| repo | truth / visible edges | level | P_exec (all) | recall (all) | P_exec (visible) | recall (visible) | F1 (visible) |", "|---|---|---|---|---|---|---|---|"]
        for r in cg:
            for lv, m in r["callgraph"]["levels"].items():
                v = m["visible"]
                L.append(f"| {r['repo']} | {r['callgraph']['truth_edges']} / {r['callgraph']['visible_edges']} | {lv} | {m['precision_executed_callers']} | {m['recall']} | {v['precision_executed_callers']} | {v['recall']} | {v['f1']} |")
    im = [r for r in results if "methods" in r.get("impact", {})]
    if im:
        L += ["", "## Impact analysis vs git history (leave-one-out, time-split)", "| repo | test pairs | method | P@5 | R@5 | P@10 | R@10 | MAP@20 |", "|---|---|---|---|---|---|---|---|"]
        for r in im:
            for name, m in r["impact"]["methods"].items():
                L.append(f"| {r['repo']} | {r['impact']['pairs']} | {name} | {m['precision@5']} | {m['recall@5']} | {m['precision@10']} | {m['recall@10']} | {m['MAP@20']} |")
        L += ["", "Share of truly co-changed files (top-10 predictions): " + "; ".join(f"{r['repo']}: " + ", ".join(f"{k} {v:.0%}" for k, v in r["impact"]["truth_coverage_top10"].items()) for r in im)]
    gr = [r for r in results if "guardrail" in r]
    if gr:
        kinds = list(gr[0]["guardrail"]["summary"])
        L += ["", "## PR guardrail: detection of seeded defects (recall)", "| repo | " + " | ".join(kinds) + " |", "|---|" + "---|" * len(kinds)]
        for r in gr:
            L.append(f"| {r['repo']} | " + " | ".join(f"{r['guardrail']['summary'][k]['recall']} ({r['guardrail']['summary'][k]['detected']}/{r['guardrail']['summary'][k]['n']})" for k in kinds) + " |")
    rc = [r for r in results if "commits" in r.get("real_commits", {})]
    if rc:
        L += ["", "## PR guardrail on real historical commits (false-positive proxy)", "| repo | commits | with ≥1 high | with ≥1 medium/high | verdicts |", "|---|---|---|---|---|"]
        for r in rc:
            x = r["real_commits"]
            L.append(f"| {r['repo']} | {x['commits']} | {x['with_high']} | {x['with_medium_or_high']} | {x['verdicts']} |")

    # Miss taxonomy table
    miss_reps = [r for r in cg if "miss_taxonomy" in r.get("callgraph", {})]
    if miss_reps:
        tax_keys = list(miss_reps[0]["callgraph"]["miss_taxonomy"])
        L += ["", "## Call-graph miss taxonomy (resolver level 2 vs dynamic trace)",
              "| repo | " + " | ".join(k.replace('_', ' ') for k in tax_keys) + " |",
              "|---|" + "---|" * len(tax_keys)]
        for r in miss_reps:
            tax = r["callgraph"]["miss_taxonomy"]
            L.append(f"| {r['repo']} | " + " | ".join(str(tax.get(k, 0)) for k in tax_keys) + " |")

    # Statistical significance & 95% bootstrap CIs
    ci_rows = []
    sig_rows = []
    for r in results:
        repo = r["repo"]
        for exp_key in ("doc2code", "callers"):
            exp_data = r.get(exp_key, {})
            rows_map = exp_data.get("rows", {})
            if rows_map:
                for mode in ("bm25", "vector", "hybrid", "graph", "auto"):
                    mode_rows = rows_map.get(mode, [])
                    vals = [row.get("sym_R@10", 0.0) for row in mode_rows if isinstance(row, dict)]
                    if vals:
                        low, high = bootstrap_ci(vals)
                        mean_val = round(sum(vals) / len(vals), 3)
                        ci_rows.append((repo, exp_key, mode, mean_val, f"[{low:.3f}, {high:.3f}]"))
                raw_pairs = []
                for m_a, m_b in [("auto", "bm25"), ("auto", "vector"), ("graph", "bm25")]:
                    a_vals = [row.get("sym_R@10", 0.0) for row in rows_map.get(m_a, []) if isinstance(row, dict)]
                    b_vals = [row.get("sym_R@10", 0.0) for row in rows_map.get(m_b, []) if isinstance(row, dict)]
                    if len(a_vals) == len(b_vals) and a_vals:
                        w, p = wilcoxon_signed_rank(a_vals, b_vals)
                        raw_pairs.append((m_a, m_b, w, p))
                if raw_pairs:
                    adj_p = holm_bonferroni([p for _, _, _, p in raw_pairs])
                    for (m_a, m_b, w, p), p_adj in zip(raw_pairs, adj_p):
                        sig_rows.append((repo, exp_key, f"{m_a} vs {m_b}", w, p, p_adj, "p < 0.05" if p_adj < 0.05 else "n.s."))

    if ci_rows:
        L += ["", "## 95% Bootstrap Confidence Intervals (symbol Recall@10)",
              "| repo | experiment | mode | mean | 95% Bootstrap CI |",
              "|---|---|---|---|---|"]
        for repo, exp, mode, mean_v, ci_str in ci_rows:
            L.append(f"| {repo} | {exp} | {mode} | {mean_v} | {ci_str} |")

    if sig_rows:
        L += ["", "## Hypothesis Testing & Significance (Paired Wilcoxon with Holm Correction)",
              "| repo | experiment | comparison | W stat | p-value | Holm adj. p | significance |",
              "|---|---|---|---|---|---|---|"]
        for repo, exp, comp, w_stat, p_val, p_adj, sig in sig_rows:
            L.append(f"| {repo} | {exp} | {comp} | {w_stat} | {p_val:.4f} | {p_adj:.4f} | {sig} |")

    L += ["", "## Low-End Hardware & Resource Profile",
          "| repo | LOC | index time (s) | peak RAM (MB) | storage (files) | CPU footprint |",
          "|---|---|---|---|---|---|"]
    for r in results:
        sc = r.get("scale", {})
        L.append(f"| {r['repo']} | {sc.get('loc', 0):,} | {sc.get('index_seconds', 0)}s | {sc.get('peak_rss_mb', 0)} MB | {sc.get('files', 0)} files | CPU-only (no GPU required) |")

    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        if cg:
            fig, ax = plt.subplots(figsize=(6, 3.6))
            for r in cg:
                lv = list(r["callgraph"]["levels"])
                ax.plot(range(len(lv)), [r["callgraph"]["levels"][k]["visible"]["recall"] for k in lv], marker="o", label=r["repo"])
            ax.set_xticks(range(4)); ax.set_xticklabels(["0 name-only", "1 scope", "2 +types", "3 +Jedi"]); ax.set_ylabel("recall of observed (visible) calls"); ax.set_xlabel("resolver level"); ax.legend(); ax.grid(alpha=.3)
            fig.tight_layout(); fig.savefig(out / "fig_resolver_recall.png", dpi=160); plt.close(fig)
        if im:
            fig, ax = plt.subplots(figsize=(6, 3.6))
            names = list(im[0]["impact"]["methods"])
            for i, nm in enumerate(names):
                ax.bar([j + i * 0.2 for j in range(len(im))], [r["impact"]["methods"][nm]["recall@10"] for r in im], 0.2, label=nm)
            ax.set_xticks([j + 0.3 for j in range(len(im))]); ax.set_xticklabels([r["repo"] for r in im]); ax.set_ylabel("recall@10"); ax.legend(); ax.grid(axis="y", alpha=.3)
            fig.tight_layout(); fig.savefig(out / "fig_impact_recall.png", dpi=160); plt.close(fig)
        d2 = [r for r in results if "doc2code" in r]
        if d2:
            fig, ax = plt.subplots(figsize=(6, 3.6))
            modes = ["bm25", "vector", "hybrid", "graph", "auto"]
            for i, m in enumerate(modes):
                ax.bar([j + i * 0.16 for j in range(len(d2))], [r["doc2code"][m].get("sym_R@10", 0) for r in d2], 0.2, label=m)
            ax.set_xticks([j + 0.3 for j in range(len(d2))]); ax.set_xticklabels([r["repo"] for r in d2]); ax.set_ylabel("symbol recall@10"); ax.legend(); ax.grid(axis="y", alpha=.3)
            fig.tight_layout(); fig.savefig(out / "fig_doc2code.png", dpi=160); plt.close(fig)
    except Exception as e:
        print("figures skipped:", e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos", default="click", help="comma list from suite_repos.json (or 'all')")
    ap.add_argument("--config", default=str(HERE / "suite_repos.json"))
    ap.add_argument("--steps", default="all")
    ap.add_argument("--out", default="results")
    ap.add_argument("--work", default="suite_work", help="where repositories are cloned")
    ap.add_argument("--depth", type=int, default=1500)
    ap.add_argument("--n", type=int, default=200, help="questions per auto-benchmark")
    ap.add_argument("--commits", type=int, default=400)
    ap.add_argument("--per-type", type=int, default=20, help="seeded mutations per defect type")
    ap.add_argument("--commit-k", type=int, default=25)
    ap.add_argument("--trace-timeout", type=int, default=900)
    ap.add_argument("--aggregate-only", action="store_true", help="rebuild report.md/summary.csv/figures from existing per-repo JSON")
    args = ap.parse_args()
    cfgs = json.loads(Path(args.config).read_text())
    if args.aggregate_only:
        cfgs = []
    elif args.repos != "all":
        want = set(args.repos.split(","))
        cfgs = [c for c in cfgs if c["name"] in want]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    Path(args.work).mkdir(parents=True, exist_ok=True)
    results = []
    for c in cfgs:
        print(f"== {c['name']}")
        try:
            r = run_repo(c, args, out)
            f = out / f"{c['name']}.json"
            if f.exists() and args.steps != "all":                  # re-running a subset of steps keeps the other results
                try:
                    old = json.loads(f.read_text())
                    old.update({k: v for k, v in r.items() if k != "steps"})
                    r = old
                except Exception:
                    pass
            f.write_text(json.dumps(r, indent=1, default=str))
            results.append(r)
        except Exception as e:
            print("   FAILED:", e)
    allr = []
    for f in sorted(out.glob("*.json")):
        if f.name.endswith("_truth.json"):
            continue
        try:
            d = json.loads(f.read_text())
            if isinstance(d, dict) and "repo" in d and "scale" in d:
                allr.append(d)
        except Exception:
            pass
    if allr:
        aggregate(allr, out)
        print(f"\nwrote {out}/report.md, summary.csv and per-repo JSON ({len(allr)} repos)")


if __name__ == "__main__":
    main()
