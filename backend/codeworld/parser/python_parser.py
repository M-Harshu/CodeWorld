"""Tree-sitter based Python parser: symbols, imports, call sites, light type hints, routes, effects."""
from __future__ import annotations
import re
import tree_sitter_python as tsp
from tree_sitter import Language, Parser

from .common import (CallRec, ImportRec, ParseResult, Symbol, PY_EFFECTS, detect_effects, is_test_path, scan_todos)

_LANG = Language(tsp.language())
_TYPING_NOISE = {"Optional", "List", "Dict", "Set", "Tuple", "Union", "Any", "Callable", "Iterable", "Iterator", "Sequence",
                 "Type", "None", "list", "dict", "set", "tuple", "str", "int", "float", "bool", "bytes", "object", "Literal",
                 "Annotated", "ClassVar", "Final", "Awaitable", "Generator", "Mapping"}
_ROUTE = re.compile(r"@\s*([\w.]+)\.(get|post|put|delete|patch|head|options|route|websocket)\(\s*[rbf]?[\"']([^\"']*)[\"']([^)]*)")
_COMPLEX = {"if_statement", "elif_clause", "for_statement", "while_statement", "except_clause", "conditional_expression",
            "boolean_operator", "for_in_clause", "case_clause", "assert_statement"}


def norm_type(t: str | None) -> str | None:
    if not t:
        return None
    t = t.strip().strip("'\"")
    for name in re.findall(r"[A-Za-z_][\w.]*", t):
        if name not in _TYPING_NOISE:
            return name
    return None


class _Walker:
    def __init__(self, path: str, src: bytes):
        self.path, self.src = path, src
        self.is_test_file = is_test_path(path)
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRec] = []
        self.calls: list[CallRec] = []
        self.routes: list[dict] = []
        self.module_effects: set[str] = set()
        self.module_types: dict[str, str] = {}
        self.module_vars: list[str] = []
        self.has_main = False
        self.file_id = f"file:{path}"

    def text(self, n) -> str:
        return self.src[n.start_byte:n.end_byte].decode("utf8", "replace")

    def sid(self, q: str) -> str:
        return f"sym:{self.path}::{q}"

    # ------------------------------------------------------------------ traversal
    def visit(self, node, ctx):
        t = node.type
        if t == "decorated_definition":
            decs = [self.text(c) for c in node.children if c.type == "decorator"]
            d = node.child_by_field_name("definition")
            if d is not None:
                self.visit_def(d, ctx, decs)
            return
        if t in ("function_definition", "class_definition"):
            self.visit_def(node, ctx, [])
            return
        if t == "import_statement":
            self.visit_import(node)
            return
        if t == "import_from_statement":
            self.visit_from(node)
            return
        if t == "if_statement" and ctx["fn"] is None and ctx["cls"] is None:
            cond = node.child_by_field_name("condition")
            if cond is not None and "__name__" in self.text(cond) and "__main__" in self.text(cond):
                self.has_main = True
        if t == "assignment":
            self.visit_assign(node, ctx)
        if t == "call":
            self.visit_call(node, ctx)
        for c in node.children:
            self.visit(c, ctx)

    # ------------------------------------------------------------------ definitions
    def visit_def(self, node, ctx, decorators):
        name_n = node.child_by_field_name("name")
        if name_n is None:
            return
        name = self.text(name_n)
        qual = f"{ctx['qual']}.{name}" if ctx["qual"] else name
        start, end = node.start_point[0] + 1, node.end_point[0] + 1
        body = node.child_by_field_name("body")
        parent_id = ctx["fn"].id if ctx["fn"] else (ctx["cls"].id if ctx["cls"] else None)
        if node.type == "class_definition":
            sup = node.child_by_field_name("superclasses")
            bases = []
            if sup is not None:
                for c in sup.named_children:
                    if c.type in ("identifier", "attribute"):
                        bases.append(self.text(c))
                    elif c.type == "subscript":                       # Generic[T] / Base[int]
                        v = c.child_by_field_name("value")
                        if v is not None:
                            bases.append(self.text(v))
            sym = Symbol(id=self.sid(qual), kind="class", name=name, qualname=qual, path=self.path, start=start, end=end,
                         parent=parent_id, signature=f"class {name}({', '.join(bases)})" if bases else f"class {name}",
                         doc=self.doc(body), decorators=decorators, loc=end - start + 1, bases=bases,
                         is_test=self.is_test_file or name.startswith("Test"))
            self.symbols.append(sym)
            if body is not None:
                self.visit(body, {"cls": sym, "fn": None, "qual": qual})
            return
        # function / method
        params = node.child_by_field_name("parameters")
        in_class = ctx["cls"] is not None and ctx["fn"] is None
        kind = "method" if in_class else "function"
        is_test = (self.is_test_file and name.startswith("test")) or (in_class and ctx["cls"].name.startswith("Test") and name.startswith("test"))
        if is_test:
            kind = "test"
        ret = node.child_by_field_name("return_type")
        sig_end = (ret.end_byte if ret is not None else (params.end_byte if params is not None else name_n.end_byte))
        signature = self.src[node.start_byte:sig_end].decode("utf8", "replace")
        signature = re.sub(r"\s+", " ", signature)[:300]
        sym = Symbol(id=self.sid(qual), kind=kind, name=name, qualname=qual, path=self.path, start=start, end=end,
                     parent=parent_id, signature=signature, doc=self.doc(body), decorators=decorators,
                     complexity=self.complexity(node), loc=end - start + 1, is_test=is_test or (self.is_test_file and kind != "class"))
        self.symbols.append(sym)
        self.read_params(params, sym)
        for d in decorators:
            m = _ROUTE.search(d)
            if m:
                verb, route = m.group(2).upper(), m.group(3)
                if verb == "ROUTE":
                    mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", m.group(4) or "")
                    verb = (re.findall(r"[A-Za-z]+", mm.group(1))[0].upper() if mm and re.findall(r"[A-Za-z]+", mm.group(1)) else "GET")
                sym.route = {"method": verb, "path": route}
        sub = {"cls": ctx["cls"], "fn": sym, "qual": qual}
        if params is not None:                                         # Depends(...) defaults etc.
            self.visit(params, sub)
        if body is not None:
            self.visit(body, sub)

    def read_params(self, params, sym: Symbol):
        if params is None:
            return
        for p in params.named_children:
            if p.type == "typed_parameter":
                ids = [c for c in p.named_children if c.type == "identifier"]
                t = p.child_by_field_name("type")
                if ids and t is not None and (nt := norm_type(self.text(t))):
                    sym.local_types[self.text(ids[0])] = nt
            elif p.type == "typed_default_parameter":
                n, t = p.child_by_field_name("name"), p.child_by_field_name("type")
                if n is not None and t is not None and (nt := norm_type(self.text(t))):
                    sym.local_types[self.text(n)] = nt

    def doc(self, body) -> str:
        if body is None:
            return ""
        for c in body.named_children:
            if c.type == "comment":
                continue
            if c.type == "expression_statement" and c.named_child_count and c.named_children[0].type == "string":
                s = c.named_children[0]
                parts = [self.text(x) for x in s.named_children if x.type == "string_content"]
                return re.sub(r"\s+", " ", " ".join(parts)).strip()[:500]
            break
        return ""

    def complexity(self, fn) -> int:
        n, stack = 1, list(fn.children)
        while stack:
            x = stack.pop()
            if x.type in ("function_definition", "class_definition", "lambda"):
                continue
            if x.type in _COMPLEX:
                n += 1
            stack.extend(x.children)
        return n

    # ------------------------------------------------------------------ imports
    def visit_import(self, node):
        for c in node.named_children:
            if c.type == "dotted_name":
                self.imports.append(ImportRec(module=self.text(c), line=node.start_point[0] + 1, kind="import"))
            elif c.type == "aliased_import":
                n, a = c.child_by_field_name("name"), c.child_by_field_name("alias")
                self.imports.append(ImportRec(module=self.text(n), alias=self.text(a) if a is not None else None,
                                              line=node.start_point[0] + 1, kind="import"))

    def visit_from(self, node):
        m = node.child_by_field_name("module_name")
        raw = self.text(m) if m is not None else ""
        level = len(raw) - len(raw.lstrip("."))
        names = []
        for c in node.children_by_field_name("name"):
            if c.type == "aliased_import":
                names.append([self.text(c.child_by_field_name("name")), self.text(c.child_by_field_name("alias"))])
            else:
                names.append([self.text(c), None])
        if any(c.type == "wildcard_import" for c in node.named_children):
            names.append(["*", None])
        self.imports.append(ImportRec(module=raw.lstrip("."), names=names, level=level, line=node.start_point[0] + 1, kind="from"))

    # ------------------------------------------------------------------ assignments (type hints)
    def visit_assign(self, node, ctx):
        left, right, typ = node.child_by_field_name("left"), node.child_by_field_name("right"), node.child_by_field_name("type")
        if left is None:
            return
        if ctx["fn"] is None and ctx["cls"] is None:
            for ident in ([left] if left.type == "identifier" else [x for x in left.named_children if x.type == "identifier"]):
                self.module_vars.append(self.text(ident))
        ttext = None
        if typ is not None:
            ttext = norm_type(self.text(typ))
        elif right is not None and right.type == "call":
            fn = right.child_by_field_name("function")
            ct = self.callee(fn) if fn is not None else None
            if ct and ct.split(".")[-1][:1].isupper():
                ttext = ct
        elif right is not None and right.type == "identifier" and ctx["fn"] is not None:
            ttext = ctx["fn"].local_types.get(self.text(right))
        if not ttext:
            return
        if left.type == "identifier":
            if ctx["fn"] is not None:
                ctx["fn"].local_types[self.text(left)] = ttext
            elif ctx["cls"] is None:
                self.module_types[self.text(left)] = ttext
            elif ctx["cls"] is not None and typ is not None:          # dataclass-style attribute
                ctx["cls"].attr_types[self.text(left)] = ttext
        elif left.type == "attribute" and ctx["cls"] is not None:
            obj, attr = left.child_by_field_name("object"), left.child_by_field_name("attribute")
            if obj is not None and self.text(obj) == "self" and attr is not None:
                ctx["cls"].attr_types[self.text(attr)] = ttext

    # ------------------------------------------------------------------ calls
    def callee(self, n) -> str | None:
        if n is None:
            return None
        if n.type == "identifier":
            return self.text(n)
        if n.type == "attribute":
            obj, attr = n.child_by_field_name("object"), n.child_by_field_name("attribute")
            a = self.text(attr)
            if obj.type in ("identifier", "attribute"):
                return f"{self.callee(obj)}.{a}"
            if obj.type == "call":
                f = obj.child_by_field_name("function")
                if f is not None and f.type == "identifier" and self.text(f) == "super":
                    return f"super.{a}"
                return f"<call>.{a}"
            return f"<expr>.{a}"
        return None

    def visit_call(self, node, ctx):
        fn = node.child_by_field_name("function")
        text = self.callee(fn)
        if not text:
            return
        owner = ctx["fn"].id if ctx["fn"] else self.file_id
        last = fn.child_by_field_name("attribute") if fn.type == "attribute" else fn
        args = node.child_by_field_name("arguments")
        str_arg, flags, first_ref = None, [], None
        if args is not None:
            for i, a in enumerate(args.named_children):
                if i == 0 and a.type == "string":
                    str_arg = " ".join(self.text(x) for x in a.named_children if x.type == "string_content")[:120]
                if i == 0 and a.type in ("identifier", "attribute"):
                    first_ref = self.text(a)
                if a.type == "keyword_argument" and self.text(a).replace(" ", "") == "shell=True":
                    flags.append("shell_true")
                if i == 0 and (a.type == "binary_operator" or (a.type == "string" and any(x.type == "interpolation" for x in a.named_children))
                               or (a.type == "call" and ".format" in self.text(a)[:80])):
                    flags.append("dynamic_arg")
        rec = CallRec(caller=owner, text=text, line=node.start_point[0] + 1, col=(last or fn).start_point[1],
                      str_arg=str_arg, flags=flags)
        self.calls.append(rec)
        eff = detect_effects(text, PY_EFFECTS)
        if eff:
            target = ctx["fn"].effects if ctx["fn"] else None
            for e in eff:
                if target is not None:
                    if e not in target:
                        target.append(e)
                else:
                    self.module_effects.add(e)
        if text.endswith("Depends") and first_ref:                    # FastAPI dependency injection edge
            self.calls.append(CallRec(caller=owner, text=first_ref, line=node.start_point[0] + 1, col=0, flags=["di"]))
        if text in ("path", "re_path", "url") and args is not None and len(args.named_children) >= 2:   # Django urls
            a0, a1 = args.named_children[0], args.named_children[1]
            if a0.type == "string" and a1.type in ("identifier", "attribute"):
                self.routes.append({"method": "ANY", "path": "/" + str_arg.lstrip("^/").rstrip("$") if str_arg is not None else "/",
                                    "handler": self.text(a1), "line": node.start_point[0] + 1})


def parse_python(path: str, src: bytes) -> ParseResult:
    res = ParseResult(path=path, language="python", is_test=is_test_path(path))
    res.loc = src.count(b"\n") + 1
    try:
        tree = Parser(_LANG).parse(src)
        w = _Walker(path, src)
        w.visit(tree.root_node, {"cls": None, "fn": None, "qual": ""})
        res.symbols, res.imports, res.calls, res.routes = w.symbols, w.imports, w.calls, w.routes
        res.has_main, res.effects, res.module_types = w.has_main, sorted(w.module_effects), w.module_types
        res.module_vars = sorted(set(w.module_vars))
        res.doc = w.doc(tree.root_node)
        if tree.root_node.has_error:
            res.error = "syntax errors present (partial parse)"
    except Exception as e:          # never let one bad file stop indexing
        res.error = f"parse failure: {e}"
    text = src.decode("utf8", "replace")
    m = re.search(r"^__all__\s*(?:\+?=)\s*[\[(]([^\])]*)[\])]", text, re.M | re.S)
    if m:
        res.all_names = re.findall(r"[\"']([A-Za-z_]\w*)[\"']", m.group(1))
    res.todos = scan_todos(text)
    return res
