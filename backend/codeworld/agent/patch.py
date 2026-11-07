"""Propose-a-patch agent (Cursor/Antigravity-style edit mode, but safe by construction).

The LLM answers with SEARCH/REPLACE edit blocks (more reliable than asking for raw diffs). CodeWorld applies them to a scratch
copy, turns the result into a unified diff, and runs the graph-based PR guardrail on it. Nothing is ever written to your repository.
"""
from __future__ import annotations
import difflib
import re

from ..analysis import diffreview

SYSTEM = """You are CodeWorld's patch writer. Make the smallest correct change that accomplishes the task, using only code shown in the context.
Output ONLY edit blocks, no prose. Format, one block per change:

path/relative/to/repo.py
<<<<<<< SEARCH
exact existing lines to replace (copy them verbatim, enough lines to be unique)
=======
the new lines
>>>>>>> REPLACE

To create a new file use an empty SEARCH section. Never invent files, functions or imports that are not in the context."""

BLOCK = re.compile(r"^([^\n<>=]+?)\n<<<<<<< SEARCH\n(.*?)\n?=======\n(.*?)\n?>>>>>>> REPLACE", re.S | re.M)


def parse_blocks(text: str) -> list[tuple[str, str, str]]:
    text = re.sub(r"```\w*\n|```", "", text)
    return [(m.group(1).strip().strip("`"), m.group(2), m.group(3)) for m in BLOCK.finditer(text)]


def apply_blocks(ctx, blocks) -> tuple[dict[str, tuple[str, str]], list[str]]:
    files: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    for path, search, replace in blocks:
        try:
            old = files[path][1] if path in files else (ctx.abs_path(path).read_text("utf8") if ctx.abs_path(path).exists() else "")
            orig = files[path][0] if path in files else old
        except Exception as e:
            errors.append(f"{path}: cannot read ({e})")
            continue
        if search.strip() == "":
            new = (old + "\n" if old and not old.endswith("\n") else old) + replace + "\n"
        else:
            n = old.count(search)
            if n != 1:
                errors.append(f"{path}: SEARCH block matched {n} times (must match exactly once). Copy the lines verbatim.")
                continue
            new = old.replace(search, replace)
        files[path] = (orig, new)
    return files, errors


def to_diff(files: dict[str, tuple[str, str]]) -> str:
    out = []
    for path, (old, new) in files.items():
        if old == new:
            continue
        a = old.splitlines(keepends=True)
        b = new.splitlines(keepends=True)
        body = list(difflib.unified_diff(a, b, f"a/{path}", f"b/{path}", n=3))
        if not old:
            out.append(f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n" + "".join(body[2:]))
        else:
            out.append(f"diff --git a/{path} b/{path}\n" + "".join(body))
    return "\n".join(x if x.endswith("\n") else x + "\n" for x in out)


def propose(ctx, task: str, retries: int = 1) -> dict:
    llm = ctx.llm
    if not llm.available:
        return {"ok": False, "diff": "", "report": "Patch proposals need an LLM (offline mode cannot write code). Configure a provider - see SETUP_AND_RUN.md section 4."}
    from ..ai.intent import classify
    hits = ctx.retriever.search(task, "graph", 8, classify(task))
    context, used = ctx.retriever.context(hits, budget_chars=11000)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Task: {task}\n\nRepository context:\n{context}"}]
    files, errors, raw = {}, [], ""
    for attempt in range(retries + 1):
        raw = llm.chat(msgs, temperature=0.1, max_tokens=1800)
        blocks = parse_blocks(raw)
        if not blocks:
            errors = ["no edit blocks found in the reply"]
        else:
            files, errors = apply_blocks(ctx, blocks)
        if not errors:
            break
        msgs += [{"role": "assistant", "content": raw}, {"role": "user", "content": "Problems: " + "; ".join(errors) + "\nFix them and output the edit blocks again."}]
    if errors or not files:
        return {"ok": False, "diff": "", "raw": raw, "report": "Could not produce an applicable patch: " + "; ".join(errors or ["empty change"])}
    diff = to_diff(files)
    review = diffreview.review_patch(ctx, diff)
    verdict = review["verdict"].upper()
    report = (f"### Proposed patch ({len(files)} file(s)) - guardrail verdict: **{verdict}**\n\n```diff\n{diff}```\n\n" + review["markdown"])
    return {"ok": True, "diff": diff, "review": {k: v for k, v in review.items() if k != "graph"}, "report": report, "raw": raw}
