"""AST-aware chunking: one chunk per function/method, one summary chunk per class and file, plus markdown sections."""
from __future__ import annotations
import hashlib
import re

from ..parser import ParseResult

MAX_CHARS = 6000


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf8", "replace")).hexdigest()


def chunk_code_file(pr: ParseResult, src_text: str) -> list[dict]:
    lines = src_text.splitlines()
    out: list[dict] = []
    fid = f"file:{pr.path}"
    by_parent: dict[str, list] = {}
    for s in pr.symbols:
        by_parent.setdefault(s.parent or fid, []).append(s)
    for s in pr.symbols:
        if s.kind == "class":
            members = by_parent.get(s.id, [])
            body = [s.signature]
            if s.doc:
                body.append(f'    """{s.doc}"""')
            body += [f"    {m.signature}   # line {m.start}" for m in members]
            out.append(dict(id=f"c:{s.id}", node_id=s.id, path=pr.path, kind="class", name=s.qualname, start=s.start, end=s.end,
                            text="\n".join(body)[:MAX_CHARS]))
        else:
            code = "\n".join(lines[s.start - 1:s.end])
            out.append(dict(id=f"c:{s.id}", node_id=s.id, path=pr.path, kind=s.kind, name=s.qualname, start=s.start, end=s.end,
                            text=code[:MAX_CHARS]))
    seen: dict[str, int] = {}
    for ch in out:                                   # redefinitions (property setters, overloads) share a qualname: keep chunk ids unique
        n = seen.get(ch["id"], 0)
        seen[ch["id"]] = n + 1
        if n:
            ch["id"] = f"{ch['id']}#{n}"
    summary = [f"# {pr.path} ({pr.language}, {pr.loc} lines)"]
    if pr.doc:
        summary.append(pr.doc)
    imps = sorted({(i.module or "") + ("." + n[0] if i.kind == "from" and n else "") for i in pr.imports for n in (i.names or [[None]])})
    if imps:
        summary.append("imports: " + ", ".join(x for x in imps if x)[:600])
    top = [s for s in pr.symbols if s.parent is None]
    if top:
        summary.append("defines: " + ", ".join(f"{s.kind} {s.name}" for s in top)[:900])
    out.append(dict(id=f"c:{fid}", node_id=fid, path=pr.path, kind="file", name=pr.path, start=1, end=pr.loc, text="\n".join(summary)))
    return out


def chunk_markdown(path: str, text: str) -> list[dict]:
    parts, cur_title, cur, start = [], path, [], 1
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"^#{1,3}\s+\S", line) and cur:
            parts.append((cur_title, start, i - 1, "\n".join(cur)))
            cur, start = [], i
        if re.match(r"^#{1,3}\s+\S", line):
            cur_title = line.lstrip("# ").strip()
        cur.append(line)
    if cur:
        parts.append((cur_title, start, start + len(cur) - 1, "\n".join(cur)))
    out = []
    for n, (title, s, e, body) in enumerate(parts):
        if len(body.strip()) < 30:
            continue
        out.append(dict(id=f"c:doc:{path}#{n}", node_id=None, path=path, kind="doc", name=title, start=s, end=e, text=body[:MAX_CHARS]))
    return out


def embed_text(c: dict) -> str:
    return f"{c['kind']} {c['name']} ({c['path']})\n{c['text']}"
