"""Question answering: retrieval -> (LLM | extractive) answer with verified citations; optional agent loop with tools."""
from __future__ import annotations
import json
import re
import time

from ..ai.intent import classify, mentions
from ..textutil import estimate_tokens, redact_secrets
from .tools import Seen, ToolError, ToolRegistry, compact

CITE = re.compile(r"\[([\w./@\-]+\.\w+):(\d+)(?:-(\d+))?\]")
SYSTEM = ("You are CodeWorld, an expert assistant that answers questions about ONE software repository using only the provided "
          "code context. Rules: (1) Ground every claim in the context. (2) Cite evidence as [path:start-end] or [path:line] using "
          "exactly the paths and line numbers shown. (3) If the context is insufficient, say what is missing - never invent files, "
          "functions or behaviour. (4) Be concise and concrete; use short bullet lists for flows.")


def check_citations(text: str, seen: Seen) -> list[dict]:
    out = []
    for m in CITE.finditer(text):
        path, s = m.group(1), int(m.group(2))
        e = int(m.group(3) or s)
        out.append({"path": path, "start": s, "end": e, "valid": seen.covers(path, s, e)})
    uniq = {(c["path"], c["start"], c["end"]): c for c in out}
    return list(uniq.values())


def _cite(path, start, end=None):
    return f"[{path}:{start}-{end}]" if end and end != start else f"[{path}:{start}]"


# ------------------------------------------------------------------------------- extractive composition
def _first_sentence(s: str) -> str:
    return re.split(r"(?<=[.!?])\s", s.strip(), maxsplit=1)[0][:220] if s else ""


def compose_extractive(ctx, question: str, intent: str, results: dict, hits) -> str:
    g = ctx.graph
    lines: list[str] = []
    if "impact" in results:
        r = results["impact"]
        if "error" in r:
            lines.append(r["error"])
        else:
            lines.append(f"**Impact of changing `{', '.join(r['target'])}`**: risk **{r['risk']['level']}** ({r['risk']['score']}/100).")
            lines += [f"- {x}" for x in r["risk"]["reasons"]]
            if r["direct"]:
                lines.append("Direct dependents: " + ", ".join(f"`{d['name']}` {_cite(d['path'], d['line'])}" for d in r["direct"][:8]))
            if r["apis"]:
                lines.append("Endpoints affected: " + ", ".join(f"`{a['api']}`" for a in r["apis"][:6]))
            lines.append("Tests that exercise it: " + (", ".join(f"`{t['name']}` {_cite(t['path'], g.node(t['id'])['start'])}" for t in r["tests"][:6]) or "none found"))
    elif "find_callers" in results or "find_callees" in results:
        key = "find_callers" if "find_callers" in results else "find_callees"
        r = results[key]
        verb = "calls" if key == "find_callers" else "is called by"
        lines.append(f"**{'Callers' if key == 'find_callers' else 'Callees'} of `{r['symbol']}`** ({r['count']}):")
        for x in r["results"][:15]:
            lines.append(f"- `{x['name']}` ({x['kind']}) {_cite(x['path'], x['line'], x['end'])}" + (f", depth {x['depth']}" if x["depth"] > 1 else ""))
        if not r["results"]:
            lines.append("None found in the static call graph (it may be called dynamically, from outside the repo, or only via an endpoint).")
    elif "find_tests" in results:
        r = results["find_tests"]
        lines.append(f"**Tests for `{', '.join(r['target'])}`**:")
        lines += [f"- `{t['name']}` {_cite(t['path'], g.node(t['id'])['start'])} (call distance {t['distance']})" for t in r["tests"][:10]]
        lines += [f"- probably `{t['path']}` ({t['via']}-based match)" for t in r["likely_tests"][:5]]
        if not r["tests"] and not r["likely_tests"]:
            lines.append("No test reaches this code.")
    elif "security_summary" in results:
        r = results["security_summary"]
        lines.append(f"**Security triage** (heuristic): {r['summary']['critical']} critical, {r['summary']['high']} high, {r['summary']['medium']} medium; {r['summary']['endpoints_at_risk']} endpoint(s) reach risky sinks.")
        for f in r["findings"][:8]:
            lines.append(f"- [{f['severity']}] {f['message']} in `{f['symbol']}` {_cite(f['path'], f['line'])}")
        for a in r["attack_surface"][:4]:
            lines.append(f"- `{a['api']}` -> " + " → ".join(a["risks"][0]["route"]) + f" ({a['risks'][0]['sink']})")
    elif "repo_overview" in results:
        r = results["repo_overview"]
        lines.append(r["summary"])
        lines.append("**Core modules:** " + ", ".join(f"`{m['path']}` {_cite(m['path'], 1, m['loc'])}" for m in r["key_modules"][:6]))
        eps = [e for e in r["entry_points"] if e["kind"] == "endpoint"]
        if eps:
            lines.append("**Endpoints:** " + ", ".join(f"`{e['name']}`" for e in eps[:10]))
    elif "explain_flow" in results:
        r = results["explain_flow"]
        lines.append(f"**Flow starting at `{r['entry']}`** (static call graph):")
        for s in r["steps"][:14]:
            doc = g.props(s["id"]).get("doc", "")
            lines.append(f"- `{s['name']}` {_cite(s['path'], s['line'], s['end'])}" + (f": {_first_sentence(doc)}" if doc else ""))
        if r["edges"]:
            lines.append("Call edges: " + "; ".join(f"{a} → {b}" for a, b in r["edges"][:14]))
    if not lines or (intent in ("locate", "explain") and hits):
        lines.append("**Most relevant code:**")
        for h in hits[:6]:
            n = g.node(h.node_id) if h.node_id else None
            doc = _first_sentence(n["props"].get("doc", "")) if n else _first_sentence(h.text.replace("#", ""))
            sig = n["props"].get("signature", "") if n else ""
            why = f" ({h.via})" if h.via else ""
            lines.append(f"- `{h.name}` {_cite(h.path, h.start, h.end)}{why}" + (f": {doc}" if doc else "") + (f"\n  `{sig[:120]}`" if sig and h.kind in ("function", "method") else ""))
    lines.append("\n_Offline mode: answer assembled from the code graph without an LLM. Configure an LLM provider for natural-language synthesis._")
    return "\n".join(lines)


# ------------------------------------------------------------------------------- offline planner
def plan_offline(ctx, question: str, intent: str, tools: ToolRegistry, trace: list) -> dict:
    g = ctx.graph
    results: dict = {}
    ment = mentions(question, g, limit=4)
    sym = ment[0] if ment else None

    def run(name, **args):
        t0 = time.time()
        try:
            r = tools.call(name, args)
        except ToolError as e:
            r = {"error": str(e)}
        trace.append({"tool": name, "args": args, "ms": int((time.time() - t0) * 1000), "summary": compact(r, 160)})
        if "error" not in r or name == "impact":
            results[name] = r
        return r

    top = None
    if intent in ("flow", "locate", "explain") or sym is None:
        sr = run("search_code", query=question, k=8, mode="graph")
        fh = [h for h in sr.get("hits", []) if h.get("node_id") and g.kind(h["node_id"]) in ("function", "method")]
        routed = [h for h in fh if g.props(h["node_id"]).get("route")]
        top = next(iter([h["node_id"] for h in (routed or fh)]), None) or next((h["node_id"] for h in sr.get("hits", []) if h.get("node_id") and g.kind(h["node_id"]) == "class"), None)
    target = sym or top
    if intent == "flow":                       # prefer a starting point that actually has a call flow
        funcs = [x for x in ment if g.kind(x) in ("function", "method", "api")]
        classes = [x for x in ment if g.kind(x) == "class"]
        pool = funcs + ([top] if top else []) + classes
        target = next((x for x in pool if g.callees(x, 2, 0.5)), target)
    if intent == "impact" and target:
        run("impact", target=target)
    elif intent == "callers" and target:
        run("find_callers", symbol=target, depth=2)
    elif intent == "callees" and target:
        run("find_callees", symbol=target, depth=2)
    elif intent == "tests" and target:
        run("find_tests", target=target)
    elif intent == "security":
        run("security_summary")
    elif intent == "overview":
        run("repo_overview")
    elif intent == "flow" and target:
        run("explain_flow", entry=target)
    return results


# ------------------------------------------------------------------------------- LLM agent loop
AGENT_PROMPT = """You can call tools to explore the repository before answering. Respond with EXACTLY one JSON object per turn:
{"tool": "<name>", "args": {...}}   to call a tool, or
{"final": "<answer with [path:start-end] citations>"}   when you have enough evidence.
Tools:
%s
Call at most %d tools. Prefer search_code first, then get_symbol / find_callers / impact as needed. Never invent paths or line numbers."""


def _parse_json(s: str) -> dict | None:
    s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.M).strip()
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            depth += s[i] == "{"
            depth -= s[i] == "}"
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except Exception:
                    break
        start = s.find("{", start + 1)
    return None


def run_llm_agent(ctx, question, tools: ToolRegistry, trace: list, history, max_steps: int = 5, active_llm=None, image: str | None = None) -> str | None:
    llm = active_llm or ctx.llm
    desc = "\n".join(f"- {t['name']}({', '.join(t['inputSchema']['properties'])}): {t['description']}" for t in tools.describe())
    msgs = [{"role": "system", "content": SYSTEM + "\n" + AGENT_PROMPT % (desc, max_steps)}]
    for h in (history or [])[-6:]:
        item = {"role": h.get("role", "user"), "content": h.get("content", "")}
        if "reasoning_details" in h and h["reasoning_details"]:
            item["reasoning_details"] = h["reasoning_details"]
        msgs.append(item)
    if image and getattr(llm, "label", "") == "gemini":
        msgs.append({"role": "user", "content": [{"type": "text", "text": question}, {"type": "image_url", "image_url": {"url": image}}]})
    else:
        msgs.append({"role": "user", "content": question})
    bad = 0
    for _ in range(max_steps + 1):
        out = llm.chat(msgs, max_tokens=900)
        obj = _parse_json(out)
        if not obj:
            bad += 1
            if bad > 1:
                return out if "[" in out else None
            msgs += [{"role": "assistant", "content": out}, {"role": "user", "content": "Reply with a single valid JSON object as specified."}]
            continue
        if "final" in obj:
            return str(obj["final"])
        name, args = obj.get("tool"), obj.get("args") or {}
        t0 = time.time()
        try:
            res = tools.call(name, args)
        except ToolError as e:
            res = {"error": str(e)}
        trace.append({"tool": name, "args": args, "ms": int((time.time() - t0) * 1000), "summary": compact(res, 160)})
        msgs += [{"role": "assistant", "content": json.dumps(obj)}, {"role": "user", "content": "TOOL RESULT:\n" + redact_secrets(compact(res, 4500))}]
    msgs.append({"role": "user", "content": 'Give your final answer now as {"final": "..."}.'})
    obj = _parse_json(llm.chat(msgs, max_tokens=900)) or {}
    return str(obj["final"]) if "final" in obj else None


# ------------------------------------------------------------------------------- public API
def ask(ctx, question: str, mode: str = "auto", agent: bool = True, history: list | None = None, k: int = 8,
        provider: str | None = None, model: str | None = None, image: str | None = None) -> dict:
    t0 = time.time()
    g = ctx.graph
    seen = Seen()
    tools = ToolRegistry(ctx, seen)
    intent = classify(question)
    trace: list[dict] = []
    notes: list[str] = []
    answer, hits = None, []

    if provider:
        from ..ai.llm import get_model_llm
        try:
            llm = get_model_llm(provider, model, ctx.settings)
        except Exception as e:
            llm = ctx.llm
            notes.append(f"Model provider '{provider}' fallback: {e}")
    else:
        llm = ctx.llm

    if agent and llm.available:
        try:
            answer = run_llm_agent(ctx, question, tools, trace, history, active_llm=llm, image=image)
        except Exception as e:
            notes.append(f"LLM agent failed ({type(e).__name__}); fell back to retrieval.")
    if answer is None:
        hits = ctx.retriever.search(question, mode, k, intent)
        for h in hits:
            seen.add(h.path, h.start, h.end)
        trace.append({"tool": "search_code", "args": {"query": question, "mode": mode, "k": k}, "ms": 0, "summary": f"{len(hits)} hits: " + ", ".join(h.name for h in hits[:5])})
        if llm.available:
            try:
                context, used = ctx.retriever.context(hits)
                prompt_text = f"Question: {question}\n\nCode context:\n{redact_secrets(context)}"
                if image:
                    user_content = [
                        {"type": "text", "text": prompt_text + "\n(An image/screenshot has been attached for visual context.)"},
                        {"type": "image_url", "image_url": {"url": image}}
                    ]
                    if getattr(llm, "label", "") != "gemini":
                        notes.append("Attached image: Switch to Gemini for full visual analysis.")
                else:
                    user_content = prompt_text

                clean_history = []
                for h in (history or [])[-8:]:
                    item = {"role": h.get("role", "user"), "content": h.get("content", "")}
                    if "reasoning_details" in h and h["reasoning_details"]:
                        item["reasoning_details"] = h["reasoning_details"]
                    clean_history.append(item)

                msgs = [{"role": "system", "content": SYSTEM}] + clean_history + [
                    {"role": "user", "content": user_content}]
                answer = llm.chat(msgs, max_tokens=900)
            except Exception as e:
                notes.append(f"LLM call failed ({type(e).__name__}: {str(e)[:80]}); showing extractive answer.")
        if answer is None:
            results = plan_offline(ctx, question, intent, tools, trace) if agent else {}
            answer = compose_extractive(ctx, question, intent, results, hits)
    if not hits:
        hits = ctx.retriever.search(question, mode, k, intent)
    cites = check_citations(answer, seen)
    valid = [c for c in cites if c["valid"]]
    highlight = [h.node_id for h in hits if h.node_id][:12]
    for c in cites:
        if c["valid"]:
            nid = g.enclosing_symbol(c["path"], c["start"])
            if nid and nid not in highlight:
                highlight.append(nid)
    resp = {"answer": answer, "citations": cites, "citation_precision": (len(valid) / len(cites)) if cites else None,
            "intent": intent, "mode": mode, "provider": llm.name if llm.available else "offline", "trace": trace, "notes": notes,
            "hits": [h.to_dict(240) for h in hits[:k]], "highlight": highlight,
            "latency_ms": int((time.time() - t0) * 1000), "approx_tokens": estimate_tokens(answer)}
    if getattr(llm, "last_reasoning_details", None):
        resp["reasoning_details"] = llm.last_reasoning_details
    return resp
