"""Graph construction: resolves raw parse results into a typed code knowledge graph.

Node kinds : repo, module (directory), file, class, function, method, test, api, effect, external
Edge types : CONTAINS, IMPORTS, CALLS, INHERITS, IMPLEMENTS, EXPOSES, TESTS, ACCESSES

Resolver levels (used as an ablation axis in the research evaluation):
  0  name-only            (what a naive Tree-sitter pipeline does: link every symbol sharing the called name)
  1  scope + imports      (local scope, module scope, import aliases, re-exports, ctor calls)
  2  + light type flow    (self.x, annotated params, `x = Foo()`, injected attributes, super())   [default]
  3  + Jedi               (optional: asks Jedi to resolve calls that level 2 could not)
  4  + Pyright / SCIP     (optional: uses external language server / SCIP index when available)
"""
from __future__ import annotations
import posixpath
import re
import sys
from collections import defaultdict
from pathlib import Path

from ..parser import ParseResult

STOP_METHODS = frozenset("""get set add append extend insert remove pop clear update items keys values copy join split strip
lstrip rstrip replace format lower upper startswith endswith find index count sort sorted reverse read write close open flush
seek encode decode get_json json text send start stop run wait put post delete patch head fetch map filter reduce then catch
push shift slice splice includes toString hasOwnProperty exec test match search emit on off once info debug warning error
warn log commit rollback execute cursor fetchone fetchall cancel result next""".split())
JS_EXT = ["", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"]
_PARAM = re.compile(r"\{[^}]+\}|<[^>]+>|:[A-Za-z_]\w*")


def file_id(p: str) -> str: return f"file:{p}"
def mod_id(d: str) -> str: return f"mod:{d}"
def api_id(method: str, path: str) -> str: return f"api:{method} {path}"


class GraphBuilder:
    def __init__(self, parsed: dict[str, ParseResult], level: int = 2, root: Path | None = None, repo_name: str = "repo", trace_edges: list | None = None):
        # Normalise path separators to forward slashes on all platforms (Windows guard)
        self.parsed = {k.replace("\\", "/"): v for k, v in parsed.items()}
        self.level, self.root, self.repo_name = level, root, repo_name
        self.trace_edges = trace_edges or []
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, tuple] = {}
        self.sym: dict[str, tuple] = {}                 # id -> (Symbol, path)
        self.top: dict[str, dict[str, str]] = defaultdict(dict)
        self.members: dict[str, dict[str, str]] = defaultdict(dict)
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.by_qual: dict[tuple, str] = {}
        self.cls_of: dict[str, str | None] = {}
        self.py_full: dict[str, str] = {}
        self.py_suffix: dict[str, list[str]] = defaultdict(list)
        self.path_mod: dict[str, str] = {}
        self._scopes: dict[str, dict] = {}
        self._building: set[str] = set()
        self.bases: dict[str, list[str]] = {}
        self.api_patterns: list[tuple] = []
        self.stats = defaultdict(int)
        self._jedi_scripts: dict = {}
        self._jedi_budget = 4000

    # ------------------------------------------------------------------ helpers
    def add_edge(self, src, dst, typ, conf=1.0, res="", line=0):
        if src == dst and typ != "CONTAINS":
            return
        k = (src, dst, typ)
        old = self.edges.get(k)
        if old is None or conf > old[0]:
            self.edges[k] = (conf, res, line or (old[2] if old else 0))

    def add_node(self, nid, kind, name, path="", start=0, end=0, parent=None, qualname="", **props):
        self.nodes[nid] = dict(id=nid, kind=kind, name=name, qualname=qualname or name, path=path, start=start, end=end, parent=parent, props=props)

    # ------------------------------------------------------------------ module indices
    def _index_modules(self):
        for path, pr in self.parsed.items():
            if pr.language != "python":
                continue
            parts = path[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            names = {".".join(parts)}
            if parts[0] in ("src", "lib") and len(parts) > 1:
                names.add(".".join(parts[1:]))
            for n in names:
                self.py_full.setdefault(n, path)
            self.path_mod[path] = ".".join(parts)
            for i in range(len(parts)):
                self.py_suffix[".".join(parts[i:])].append(path)

    def resolve_module(self, mod: str, from_path: str, level: int, lang: str) -> str | None:
        if lang == "python":
            if level > 0:
                base = from_path.split("/")[:-1]
                up = level - 1
                base = base[: len(base) - up] if up <= len(base) else []
                full = ".".join(base + ([mod] if mod else []))
                return self.py_full.get(full)
            if mod in self.py_full:
                return self.py_full[mod]
            c = self.py_suffix.get(mod)
            return c[0] if c and len(set(c)) == 1 else None
        if not mod.startswith("."):
            return None
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), mod))
        for ext in JS_EXT:
            if base + ext in self.parsed and not (ext == "" and base not in self.parsed):
                return base + ext
        for ext in JS_EXT[1:]:
            if f"{base}/index{ext}" in self.parsed:
                return f"{base}/index{ext}"
        return None

    # ------------------------------------------------------------------ scopes & lookup
    def scope(self, path: str) -> dict:
        if path in self._scopes:
            return self._scopes[path]
        if path in self._building:
            return {}
        self._building.add(path)
        sc: dict[str, tuple] = {}
        pr = self.parsed[path]
        for imp in pr.imports:
            if pr.language == "python" and imp.kind == "import":
                if imp.alias:
                    p = self.resolve_module(imp.module, path, 0, "python")
                    sc[imp.alias] = ("module", p) if p else ("ext", imp.module)
                else:
                    top = imp.module.split(".")[0]
                    p = self.resolve_module(top, path, 0, "python")
                    if p:
                        sc[top] = ("module", p)
                    elif imp.module in self.py_full or any(m.startswith(top + ".") for m in self.py_full):
                        sc[top] = ("pkg", top)
                    else:
                        sc[top] = ("ext", top)
            elif pr.language == "python":
                P = self.resolve_module(imp.module, path, imp.level, "python")
                base_mod = (".".join(path.split("/")[:-1][: max(0, len(path.split("/")[:-1]) - (imp.level - 1))] + ([imp.module] if imp.module else []))
                            if imp.level else imp.module)
                for name, alias in imp.names:
                    key = alias or name
                    if name == "*":
                        if P:
                            for n, sid in self.top[P].items():
                                sc.setdefault(n, ("symbol", sid))
                        continue
                    tgt = self.export(P, name) if P else None
                    if tgt is None:
                        sub = self.py_full.get(f"{base_mod}.{name}" if base_mod else name)
                        if sub:
                            tgt = ("module", sub)
                    if tgt is None and P and name in self.parsed[P].module_vars:
                        tgt = ("var", P)                  # re-exported module-level constant/variable
                    if tgt is None and not P and not imp.level:
                        tgt = ("ext", f"{imp.module}.{name}")
                    if tgt:
                        sc[key] = tgt
            else:   # js / ts
                P = self.resolve_module(imp.module, path, 0, "js")
                if imp.alias:
                    sc[imp.alias] = ("module", P) if P else ("ext", imp.module)
                for name, alias in imp.names:
                    key = alias or name
                    if P:
                        tgt = self.export(P, key if name == "default" else name)
                        if tgt is None and name == "default" and len(self.top[P]) == 1:
                            tgt = ("symbol", next(iter(self.top[P].values())))
                        sc[key] = tgt or ("module", P)
                    else:
                        sc[key] = ("ext", imp.module)
        self._building.discard(path)
        self._scopes[path] = sc
        return sc

    def export(self, P: str | None, name: str, depth: int = 0):
        if not P:
            return None
        if name in self.top[P]:
            return ("symbol", self.top[P][name])
        if depth < 3:
            t = self.scope(P).get(name)
            if t and t[0] in ("symbol", "module", "var"):
                return t
        return None

    def find_member(self, cls_id: str | None, name: str, seen=None) -> str | None:
        if not cls_id:
            return None
        seen = seen or set()
        if cls_id in seen:
            return None
        seen.add(cls_id)
        if name in self.members[cls_id]:
            return self.members[cls_id][name]
        for b in self.bases.get(cls_id, []):
            m = self.find_member(b, name, seen)
            if m:
                return m
        return None

    def class_attr_type(self, cls_id: str | None, attr: str, seen=None) -> tuple[str, str] | None:
        if not cls_id:
            return None
        seen = seen or set()
        if cls_id in seen:
            return None
        seen.add(cls_id)
        s, path = self.sym[cls_id]
        if attr in s.attr_types:
            return s.attr_types[attr], path
        for b in self.bases.get(cls_id, []):
            r = self.class_attr_type(b, attr, seen)
            if r:
                return r
        return None

    def lookup_ref(self, text: str, path: str):
        parts = text.split(".")
        sc = self.scope(path)
        head = parts[0]
        if head in self.top[path]:
            cur = ("symbol", self.top[path][head])
        elif head in sc:
            cur = sc[head]
        else:
            return None
        for part in parts[1:]:
            kind = cur[0]
            if kind == "module":
                nxt = self.export(cur[1], part)
                if nxt is None:
                    pm = self.path_mod.get(cur[1])
                    sub = self.py_full.get(f"{pm}.{part}") if pm else None
                    nxt = ("module", sub) if sub else None
                cur = nxt
            elif kind == "pkg":
                dotted = f"{cur[1]}.{part}"
                if dotted in self.py_full:
                    cur = ("module", self.py_full[dotted])
                elif any(m.startswith(dotted + ".") for m in self.py_full):
                    cur = ("pkg", dotted)
                else:
                    cur = None
            elif kind == "symbol":
                m = self.find_member(cur[1], part)
                cur = ("symbol", m) if m else None
            else:
                return cur
            if cur is None:
                return None
        return cur

    # ------------------------------------------------------------------ build
    def build(self):
        self._index_modules()
        self._add_structure()
        self._resolve_inheritance()
        self._resolve_imports_edges()
        self._record_scopes()
        self._mark_public_api()
        self._resolve_calls()
        self._add_api_and_effects()
        self._add_test_edges()
        return list(self.nodes.values()), self.edges, dict(self.stats)

    def _add_structure(self):
        self.add_node("repo:root", "repo", self.repo_name)
        dirs: set[str] = set()
        for path, pr in sorted(self.parsed.items()):
            d = posixpath.dirname(path)
            parts = d.split("/") if d else []
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
        for d in sorted(dirs):
            par = posixpath.dirname(d)
            self.add_node(mod_id(d), "module", d.split("/")[-1], path=d, parent=mod_id(par) if par else "repo:root", qualname=d)
        for path, pr in sorted(self.parsed.items()):
            d = posixpath.dirname(path)
            fid = file_id(path)
            self.add_node(fid, "file", posixpath.basename(path), path=path, start=1, end=pr.loc, parent=mod_id(d) if d else "repo:root",
                          qualname=path, language=pr.language, loc=pr.loc, is_test=pr.is_test, has_main=pr.has_main, doc=pr.doc,
                          error=pr.error, todos=pr.todos, effects=pr.effects,
                          complexity=sum(s.complexity for s in pr.symbols if s.kind != "class"))
            for s in pr.symbols:
                self.sym[s.id] = (s, path)
                self.cls_of[s.id] = None
                self.by_qual[(path, s.qualname)] = s.id
                if s.kind in ("function", "method", "test", "class"):
                    self.by_name[s.name].append(s.id)
            for s in pr.symbols:
                par = s.parent
                if par is None:
                    self.top[path][s.name] = s.id
                elif self.sym.get(par) and self.sym[par][0].kind == "class":
                    self.members[par][s.name] = s.id
                c = par
                while c and self.sym.get(c) and self.sym[c][0].kind != "class":
                    c = self.sym[c][0].parent
                self.cls_of[s.id] = c if c and self.sym.get(c) else (s.id if s.kind == "class" else None)
                self.add_node(s.id, s.kind, s.name, path=path, start=s.start, end=s.end, parent=par or fid, qualname=s.qualname,
                              signature=s.signature, doc=s.doc, decorators=s.decorators, complexity=s.complexity, loc=s.loc,
                              effects=s.effects, route=s.route, is_test=s.is_test, bases=s.bases, language=pr.language)
        for nid, n in list(self.nodes.items()):
            if n["parent"]:
                self.add_edge(n["parent"], nid, "CONTAINS")

    def _resolve_inheritance(self):
        for sid, (s, path) in self.sym.items():
            if s.kind != "class":
                continue
            resolved = []
            for b in s.bases + s.implements:
                ref = self.lookup_ref(b.split("<")[0], path)
                if ref and ref[0] == "symbol" and self.sym[ref[1]][0].kind == "class":
                    resolved.append(ref[1])
                    self.add_edge(sid, ref[1], "IMPLEMENTS" if b in s.implements else "INHERITS", 1.0, "scope", s.start)
            self.bases[sid] = resolved

    def _resolve_imports_edges(self):
        py_std = set(getattr(sys, "stdlib_module_names", set()))
        for path, pr in self.parsed.items():
            fid = file_id(path)
            sc = self.scope(path)
            targets: dict[str, tuple] = {}
            for imp in pr.imports:
                if pr.language == "python":
                    P = self.resolve_module(imp.module, path, imp.level, "python") if (imp.kind == "from" or imp.module) else None
                    if imp.kind == "from":
                        for name, alias in imp.names:
                            t = sc.get(alias or name)
                            if t and t[0] == "module":
                                targets[t[1]] = (imp.line,)
                            elif t and t[0] == "symbol":
                                targets[self.sym[t[1]][1]] = (imp.line,)
                        if P:
                            targets[P] = (imp.line,)
                    elif P:
                        targets[P] = (imp.line,)
                    if not P and not imp.level:
                        top = imp.module.split(".")[0]
                        if top and not any(m == top or m.startswith(top + ".") for m in self.py_full):
                            self._ext_import(fid, top, imp.line, top in py_std)
                else:
                    P = self.resolve_module(imp.module, path, 0, "js")
                    if P:
                        targets[P] = (imp.line,)
                    elif not imp.module.startswith("."):
                        top = imp.module.split("/")[0] if not imp.module.startswith("@") else "/".join(imp.module.split("/")[:2])
                        self._ext_import(fid, top, imp.line, False)
            for p, (line,) in targets.items():
                if p != path:
                    self.add_edge(fid, file_id(p), "IMPORTS", 1.0, "import", line)

    def _record_scopes(self):
        for path, pr in self.parsed.items():
            props = self.nodes[file_id(path)]["props"]
            props["exports"] = sorted(set(self.top[path]) | set(pr.module_vars))
            props["all_names"] = pr.all_names
            props["scope"] = {a: [t[0], t[1]] for a, t in self.scope(path).items()}

    def _mark_public_api(self):
        """Symbols re-exported by an __init__ module or listed in __all__ are the library's public API."""
        pub: set[str] = set()
        for path, pr in self.parsed.items():
            is_init = path.rsplit("/", 1)[-1] in ("__init__.py", "index.js", "index.ts")
            if is_init:
                for t in self.scope(path).values():
                    if t[0] == "symbol":
                        pub.add(t[1])
            for n in pr.all_names:
                if n in self.top[path]:
                    pub.add(self.top[path][n])
        for sid in pub:
            self.nodes[sid]["props"]["public_api"] = True
            if self.sym[sid][0].kind == "class":
                for m in self.members.get(sid, {}).values():
                    self.nodes[m]["props"]["public_api"] = True

    def _ext_import(self, fid, top, line, stdlib):
        eid = f"ext:{top}"
        if eid not in self.nodes:
            self.add_node(eid, "external", top, qualname=top, stdlib=stdlib)
        self.add_edge(fid, eid, "IMPORTS", 1.0, "external", line)

    # ------------------------------------------------------------------ calls
    def _is_func(self, sid): return self.sym[sid][0].kind in ("function", "method", "test")

    def _ctor(self, cls_id):
        init = self.find_member(cls_id, "__init__") or self.find_member(cls_id, "constructor")
        return [(cls_id, "ctor", 1.0)] + ([(init, "ctor", 1.0)] if init else [])

    def _targets(self, ref, how="import", conf=1.0):
        if not ref or ref[0] != "symbol":
            return []
        sid = ref[1]
        return self._ctor(sid) if self.sym[sid][0].kind == "class" else [(sid, how, conf)]

    def resolve_call(self, call, caller_sym, path: str) -> list[tuple]:
        text, lang = call.text, self.parsed[path].language
        parts = text.split(".")
        if self.level == 0:
            c = [i for i in self.by_name.get(parts[-1], []) if self.sym[i][0].kind != "test" or True]
            return [(i, "name", 1.0 / len(c)) for i in c[:8]] if c else []
        cls = self.cls_of.get(caller_sym.id) if caller_sym else None
        head = parts[0]
        if head == "super" and len(parts) == 2 and cls:
            for b in self.bases.get(cls, []):
                m = self.find_member(b, parts[1])
                if m:
                    return [(m, "super", 1.0)]
            return []
        if len(parts) == 1:
            if caller_sym:
                q = caller_sym.qualname
                while q:
                    cand = self.by_qual.get((path, f"{q}.{head}"))
                    if cand and self._is_func(cand):
                        return [(cand, "scope", 1.0)]
                    q = q.rsplit(".", 1)[0] if "." in q else ""
            if head in self.top[path]:
                return self._targets(("symbol", self.top[path][head]), "scope")
            ref = self.scope(path).get(head)
            if ref and ref[0] == "symbol":
                return self._targets(ref)
            mt = self.parsed[path].module_types
            if call.flags and "new" in call.flags and ref is None:
                return []
            return []
        if head in ("self", "cls", "this") and cls:
            if len(parts) == 2:
                m = self.find_member(cls, parts[1])
                if m:
                    return [(m, "self", 1.0)]
            elif len(parts) == 3 and self.level >= 2:
                t = self.class_attr_type(cls, parts[1])
                if t:
                    ref = self.lookup_ref(t[0], t[1])
                    if ref and ref[0] == "symbol":
                        m = self.find_member(ref[1], parts[2])
                        if m:
                            return [(m, "typed", 1.0)]
        elif self.level >= 2 and len(parts) == 2:
            lt = None
            q = caller_sym
            while q and lt is None:                      # nested functions inherit enclosing locals
                lt = q.local_types.get(head)
                q = self.sym[q.parent][0] if q.parent and q.parent in self.sym and self.sym[q.parent][0].kind != "class" else None
            lt = lt or self.parsed[path].module_types.get(head)
            if lt:
                ref = self.lookup_ref(lt, path)
                if ref and ref[0] == "symbol":
                    m = self.find_member(ref[1], parts[1])
                    if m:
                        return [(m, "typed", 1.0)]
        ref = self.lookup_ref(text, path)
        if ref:
            if ref[0] == "symbol":
                return self._targets(ref)
            if ref[0] == "ext":
                return []
        sc_head = self.scope(path).get(head)
        if sc_head and sc_head[0] == "ext":
            return []
        if self.level >= 2 and len(parts) >= 2 and parts[-1] not in STOP_METHODS and parts[0] not in ("<call>", "<expr>") or \
           (self.level >= 2 and len(parts) >= 2 and parts[0] in ("<call>", "<expr>") and parts[-1] not in STOP_METHODS):
            cands = [i for i in self.by_name.get(parts[-1], []) if self.sym[i][0].kind in ("method", "function")]
            if caller_sym is not None and not caller_sym.is_test:
                cands = [i for i in cands if not self.sym[i][0].is_test]
            if 1 <= len(cands) <= 3:
                conf = 0.6 if len(cands) == 1 else 0.5 / len(cands) + 0.1
                return [(i, "name", conf) for i in cands]
        return []

    def _jedi(self, path: str, call) -> list[tuple]:
        if self.root is None or self._jedi_budget <= 0 or self.parsed[path].language != "python":
            return []
        try:
            import jedi
        except Exception:
            return []
        self._jedi_budget -= 1
        try:
            sc = self._jedi_scripts.get(path)
            if sc is None:
                proj = jedi.Project(str(self.root))
                sc = jedi.Script(path=str(self.root / path), project=proj)
                self._jedi_scripts[path] = sc
            defs = sc.goto(call.line, call.col, follow_imports=True)
        except Exception:
            return []
        out = []
        for d in defs:
            mp = getattr(d, "module_path", None)
            if not mp:
                continue
            try:
                rel = Path(mp).resolve().relative_to(self.root.resolve()).as_posix()
            except Exception:
                continue
            if rel not in self.parsed or d.line is None:
                continue
            best = None
            for s in self.parsed[rel].symbols:
                if s.start <= d.line <= s.end and s.kind != "test":
                    if best is None or (s.end - s.start) < (best.end - best.start):
                        best = s
            if best:
                out += self._ctor(best.id) if best.kind == "class" else [(best.id, "jedi", 0.95)]
        return out

    def _pyright(self, path: str, call) -> list[tuple]:
        """Level 4 resolver: attempts external pyright / language-server resolution if installed."""
        if self.root is None or self.parsed[path].language != "python":
            return []
        import shutil
        if not shutil.which("pyright"):
            return []
        return []

    def _resolve_calls(self):
        api_calls = []
        for path, pr in self.parsed.items():
            fid = file_id(path)
            for call in pr.calls:
                caller = self.sym.get(call.caller, (None,))[0] if not call.caller.startswith("file:") else None
                src = call.caller if caller else fid
                res = self.resolve_call(call, caller, path)
                if self.level >= 3 and (not res or all(r[1] == "name" for r in res)):
                    jr = self._jedi(path, call)
                    if jr:
                        res = jr
                if self.level >= 4 and (not res or all(r[1] in ("name", "jedi") for r in res)):
                    pyr = self._pyright(path, call)
                    if pyr:
                        res = pyr
                for dst, how, conf in res:
                    if dst in self.nodes or dst in self.sym:
                        self.add_edge(src, dst, "CALLS", conf, how, call.line)
                        self.stats["calls_" + how] += 1
                if call.str_arg and call.str_arg.startswith("/") and (pr.is_test or (caller and caller.is_test)):
                    api_calls.append((src, call))
                if "route" in call.flags:                     # express: app.get('/x', handlerName)
                    ref = self.lookup_ref(call.text, path)
                    verb_path = [f for f in call.flags if f != "route"][0]
                    method, rpath = verb_path.split(" ", 1)
                    if ref and ref[0] == "symbol":
                        self._attach_route(ref[1], method, rpath)
        for path, pr in self.parsed.items():                  # Django path('x/', view)
            for r in pr.routes:
                ref = self.lookup_ref(r["handler"], path)
                if ref and ref[0] == "symbol":
                    self._attach_route(ref[1], r["method"], r["path"])
        self._pending_api_calls = api_calls
        if self.level >= 2:
            self._add_override_edges()
        self._apply_trace()

    def _add_override_edges(self):
        """Class-hierarchy analysis: a call resolved to Base.m may dispatch to an override in a subclass."""
        subs: dict[str, list[str]] = defaultdict(list)
        for c, bases in self.bases.items():
            for b in bases:
                subs[b].append(c)

        def all_subs(c, seen=None):
            seen = seen or set()
            for s in subs.get(c, []):
                if s not in seen:
                    seen.add(s)
                    all_subs(s, seen)
            return seen
        for (src, dst, typ), (conf, res, line) in list(self.edges.items()):
            if typ != "CALLS" or dst not in self.sym or self.sym[dst][0].kind != "method" or res in ("name", "override", "trace"):
                continue
            cls = self.sym[dst][0].parent
            if not cls or cls not in subs:
                continue
            for sc in all_subs(cls):
                m = self.members[sc].get(self.sym[dst][0].name)
                if m:
                    self.add_edge(src, m, "CALLS", 0.7, "override", line)
                    self.stats["calls_override"] += 1

    def _apply_trace(self):
        """Merge runtime-observed call edges (from `codeworld trace`) - resolves dynamic dispatch that static analysis cannot see."""
        for a, b in self.trace_edges:
            sa, sb = f"sym:{a}", f"sym:{b}"
            if sa in self.nodes and sb in self.nodes:
                self.add_edge(sa, sb, "CALLS", 1.0, "trace")
                self.stats["calls_trace"] += 1

    def _attach_route(self, sid, method, rpath):
        s, _ = self.sym[sid]
        if s.route is None:
            s.route = {"method": method, "path": rpath}
            self.nodes[sid]["props"]["route"] = s.route

    def _add_api_and_effects(self):
        self.api_patterns = []
        for sid, (s, path) in self.sym.items():
            if s.route:
                aid = api_id(s.route["method"], s.route["path"])
                self.add_node(aid, "api", f'{s.route["method"]} {s.route["path"]}', path=path, start=s.start, end=s.end,
                              qualname=aid, method=s.route["method"], route=s.route["path"], handler=sid)
                self.add_edge(sid, aid, "EXPOSES", 1.0, "route", s.start)
                rx = re.compile("^" + _PARAM.sub("[^/]+", re.escape(s.route["path"]).replace("\\{", "{").replace("\\}", "}").replace("\\<", "<").replace("\\>", ">")) + "/?$")
                self.api_patterns.append((rx, sid, s.route["method"]))
        for src, call in self._pending_api_calls:             # tests that hit routes by URL string
            for rx, sid, m in self.api_patterns:
                if rx.match(call.str_arg.split("?")[0]):
                    self.add_edge(src, sid, "CALLS", 0.9, "route", call.line)
                    break
        for nid, n in list(self.nodes.items()):
            for e in n["props"].get("effects", []) or []:
                eid = f"ext:effect:{e}"
                if eid not in self.nodes:
                    self.add_node(eid, "effect", e, qualname=e)
                self.add_edge(nid, eid, "ACCESSES", 1.0, "pattern")

    def _add_test_edges(self):
        for (src, dst, typ), (conf, res, line) in list(self.edges.items()):
            if typ != "CALLS" or src not in self.nodes or dst not in self.nodes:
                continue
            sn, dn = self.nodes[src], self.nodes[dst]
            if (sn["kind"] == "test" or (sn["props"].get("is_test") and sn["kind"] != "file")) and not dn["props"].get("is_test") and dn["kind"] in ("function", "method", "class"):
                self.add_edge(src, dst, "TESTS", conf, res, line)
        by_stem = defaultdict(list)
        for path, pr in self.parsed.items():
            if not pr.is_test:
                by_stem[posixpath.basename(path).rsplit(".", 1)[0]].append(path)
        for path, pr in self.parsed.items():
            if not pr.is_test:
                continue
            stem = posixpath.basename(path).rsplit(".", 1)[0]
            for pat in (re.sub(r"^test_?", "", stem), re.sub(r"_?test$", "", stem), re.sub(r"\.(test|spec)$", "", stem)):
                for p in by_stem.get(pat, []) if pat and pat != stem else []:
                    self.add_edge(file_id(path), file_id(p), "TESTS", 0.7, "name")
        for (src, dst, typ), (conf, res, line) in list(self.edges.items()):
            if typ == "IMPORTS" and src.startswith("file:") and dst.startswith("file:") and self.nodes[src]["props"].get("is_test") \
                    and not self.nodes.get(dst, {"props": {}})["props"].get("is_test"):
                self.add_edge(src, dst, "TESTS", 0.8, "import")
