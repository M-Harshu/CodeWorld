"""Mine git history: churn, authorship (bus factor) and co-change coupling. Gracefully absent when there is no .git."""
from __future__ import annotations
import itertools
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

SEP = "\x1e"


def git_available(src: Path) -> bool:
    return (src / ".git").exists()


def run_git(src: Path, args: list[str], timeout: int = 120) -> str:
    r = subprocess.run(["git", "-C", str(src), *args], capture_output=True, timeout=timeout, check=False)
    return r.stdout.decode("utf8", "replace")


def read_history(src: Path, limit: int = 3000) -> list[dict]:
    out = run_git(src, ["log", f"-n{limit}", "--no-merges", "--name-only", "--no-renames",
                        f"--pretty=format:{SEP}%H|%at|%an|%s"], timeout=180)
    commits = []
    for block in out.split(SEP):
        block = block.strip("\n")
        if not block:
            continue
        head, *files = block.split("\n")
        parts = head.split("|", 3)
        if len(parts) < 4:
            continue
        files = [f for f in files if f.strip()]
        commits.append({"sha": parts[0], "ts": int(parts[1] or 0), "author": parts[2], "subject": parts[3], "files": files})
    return commits


def mine(src: Path, limit: int = 3000, max_files_per_commit: int = 40) -> dict:
    commits = read_history(src, limit)
    churn, authors, co = Counter(), defaultdict(Counter), Counter()
    last = {}
    for c in commits:
        for f in c["files"]:
            churn[f] += 1
            authors[f][c["author"]] += 1
            last[f] = max(last.get(f, 0), c["ts"])
        fs = sorted(set(c["files"]))
        if 2 <= len(fs) <= max_files_per_commit:
            for a, b in itertools.combinations(fs, 2):
                co[(a, b)] += 1
    numstat = run_git(src, ["log", f"-n{limit}", "--no-merges", "--numstat", "--pretty=format:"], timeout=180)
    added, deleted = Counter(), Counter()
    for line in numstat.splitlines():
        p = line.split("\t")
        if len(p) == 3 and p[0].isdigit() and p[1].isdigit():
            added[p[2]] += int(p[0])
            deleted[p[2]] += int(p[1])
    files = []
    for f, n in churn.items():
        a = authors[f]
        files.append((f, n, len(a), (a.most_common(1)[0][1] / n) if n else 0.0, added[f], deleted[f], last.get(f, 0)))
    return {"commits": commits, "files": files, "cochange": [(a, b, n) for (a, b), n in co.items() if n >= 2]}


def store(con, data: dict) -> None:
    con.execute("DELETE FROM git_file"); con.execute("DELETE FROM git_cochange"); con.execute("DELETE FROM git_commits")
    con.executemany("INSERT INTO git_file VALUES (?,?,?,?,?,?,?)", data["files"])
    con.executemany("INSERT INTO git_cochange VALUES (?,?,?)", data["cochange"])
    con.executemany("INSERT OR REPLACE INTO git_commits VALUES (?,?,?,?,?)",
                    [(c["sha"], c["ts"], c["author"], c["subject"][:200], "\n".join(c["files"][:200])) for c in data["commits"]])


def cochange_for(con, path: str, min_support: int = 2, top: int = 15) -> list[dict]:
    """Files that historically change together with `path` (support count + confidence = n / commits(path))."""
    rows = con.execute("SELECT a,b,n FROM git_cochange WHERE a=? OR b=?", (path, path)).fetchall()
    base = con.execute("SELECT commits FROM git_file WHERE path=?", (path,)).fetchone()
    nb = base["commits"] if base else 0
    out = []
    for r in rows:
        other = r["b"] if r["a"] == path else r["a"]
        if r["n"] >= min_support:
            out.append({"path": other, "support": r["n"], "confidence": round(r["n"] / nb, 3) if nb else 0.0})
    out.sort(key=lambda x: (-x["confidence"], -x["support"]))
    return out[:top]
