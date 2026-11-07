"""Tree-sitter based JavaScript / TypeScript parser (functions, classes, imports, calls, Express routes)."""
from __future__ import annotations
import re
import tree_sitter_javascript as tsj
import tree_sitter_typescript as tst
from tree_sitter import Language, Parser

from .common import (CallRec, ImportRec, ParseResult, Symbol, JS_EFFECTS, detect_effects, is_test_path, scan_todos)

_JS, _TS, _TSX = Language(tsj.language()), Language(tst.language_typescript()), Language(tst.language_tsx())
_FUNC = {"arrow_function", "function_expression", "function", "generator_function"}
_COMPLEX = {"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "switch_case", "catch_clause", "ternary_expression"}
_VERBS = {"get", "post", "put", "delete", "patch", "all", "use", "head", "options"}


class _Walker:
    def __init__(self, path: str, src: bytes):
        self.path, self.src = path, src
        self.is_test_file = is_test_path(path)
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRec] = []
        self.calls: list[CallRec] = []
        self.module_effects: set[str] = set()
        self.module_types: dict[str, str] = {}
        self.module_vars: list[str] = []
        self.file_id = f"file:{path}"

    def text(self, n) -> str:
        return self.src[n.start_byte:n.end_byte].decode("utf8", "replace")

    def sid(self, q):
        return f"sym:{self.path}::{q}"

    def complexity(self, fn) -> int:
        n, stack = 1, list(fn.children)
        while stack:
            x = stack.pop()
            if x.type in _COMPLEX:
                n += 1
            elif x.type == "binary_expression":
                op = x.child_by_field_name("operator")
                if op is not None and self.text(op) in ("&&", "||", "??"):
                    n += 1
            if x.type in ("function_declaration", "class_declaration"):
                continue
            stack.extend(x.children)
        return n

    def callee(self, n) -> str | None:
        if n is None:
            return None
        t = n.type
        if t in ("identifier", "property_identifier"):
            return self.text(n)
        if t == "this":
            return "this"
        if t == "super":
            return "super"
        if t == "member_expression":
            o, p = n.child_by_field_name("object"), n.child_by_field_name("property")
            oc = self.callee(o)
            return f"{oc or '<expr>'}.{self.text(p)}" if p is not None else None
        if t in ("await_expression", "parenthesized_expression", "non_null_expression") and n.named_child_count:
            return self.callee(n.named_children[0])
        return None

    def add_symbol(self, kind, name, node, ctx, **kw) -> Symbol:
        qual = f"{ctx['qual']}.{name}" if ctx["qual"] else name
        s, e = node.start_point[0] + 1, node.end_point[0] + 1
        parent = ctx["fn"].id if ctx["fn"] else (ctx["cls"].id if ctx["cls"] else None)
        sym = Symbol(id=self.sid(qual), kind=kind, name=name, qualname=qual, path=self.path, start=s, end=e, parent=parent,
                     loc=e - s + 1, is_test=self.is_test_file, **kw)
        if kind != "class":
            sym.complexity = self.complexity(node)
        self.symbols.append(sym)
        return sym

    def visit(self, node, ctx):
        t = node.type
        if t in ("function_declaration", "generator_function_declaration"):
            self.visit_fn(node, ctx, node.child_by_field_name("name"))
            return
        if t in ("class_declaration", "abstract_class_declaration", "class"):
            self.visit_class(node, ctx)
            return
        if t == "interface_declaration":
            n = node.child_by_field_name("name")
            if n is not None:
                self.add_symbol("class", self.text(n), node, ctx, signature=f"interface {self.text(n)}")
            return
        if t == "method_definition":
            self.visit_fn(node, ctx, node.child_by_field_name("name"), method=True)
            return
        if t in ("public_field_definition", "field_definition"):
            v = node.child_by_field_name("value")
            n = node.child_by_field_name("name") or node.child_by_field_name("property")
            if v is not None and v.type in _FUNC and n is not None:
                self.visit_fn(v, ctx, n, method=True)
                return
        if t == "variable_declarator":
            n, v = node.child_by_field_name("name"), node.child_by_field_name("value")
            if n is not None and n.type == "identifier" and ctx["fn"] is None and ctx["cls"] is None:
                self.module_vars.append(self.text(n))
            if n is not None and v is not None:
                if n.type == "identifier" and v.type == "new_expression":
                    ct = self.callee(v.child_by_field_name("constructor"))
                    if ct:
                        (ctx["fn"].local_types if ctx["fn"] else self.module_types)[self.text(n)] = ct
                if n.type == "identifier" and v.type in _FUNC and ctx["fn"] is None:
                    self.visit_fn(v, ctx, n)
                    return
                if v.type == "call_expression" and self.callee(v.child_by_field_name("function")) == "require":
                    a = v.child_by_field_name("arguments")
                    if a is not None and a.named_child_count and a.named_children[0].type == "string":
                        mod = self.text(a.named_children[0]).strip("'\"`")
                        if n.type == "identifier":
                            self.imports.append(ImportRec(module=mod, alias=self.text(n), line=node.start_point[0] + 1, kind="js"))
                        elif n.type == "object_pattern":
                            names = [[self.text(c), None] for c in n.named_children if c.type in ("shorthand_property_identifier_pattern",)]
                            self.imports.append(ImportRec(module=mod, names=names, line=node.start_point[0] + 1, kind="js"))
                        return
        if t == "import_statement":
            self.visit_import(node)
            return
        if t == "assignment_expression" and ctx["cls"] is not None:
            l, r = node.child_by_field_name("left"), node.child_by_field_name("right")
            if l is not None and r is not None and l.type == "member_expression" and r.type == "new_expression":
                c = self.callee(r.child_by_field_name("constructor"))
                if c and self.text(l.child_by_field_name("object")) == "this":
                    ctx["cls"].attr_types[self.text(l.child_by_field_name("property"))] = c
        if t == "call_expression":
            if self.visit_call(node, ctx):
                return
        elif t == "new_expression":
            c = self.callee(node.child_by_field_name("constructor"))
            if c:
                self.calls.append(CallRec(caller=(ctx["fn"].id if ctx["fn"] else self.file_id), text=c, line=node.start_point[0] + 1,
                                          col=node.child_by_field_name("constructor").start_point[1], flags=["new"]))
        for c in node.children:
            self.visit(c, ctx)

    def visit_fn(self, node, ctx, name_node, method=False):
        name = self.text(name_node) if name_node is not None else "<anonymous>"
        in_class = ctx["cls"] is not None and ctx["fn"] is None
        kind = "method" if (method or in_class) and in_class else "function"
        sig = re.sub(r"\s+", " ", self.src[node.start_byte:(node.child_by_field_name("body") or node).start_byte].decode("utf8", "replace"))[:300]
        sym = self.add_symbol(kind, name, node, ctx, signature=sig.strip())
        params = node.child_by_field_name("parameters")
        if params is not None:
            for p in params.named_children:
                tn = None
                for c in p.named_children:
                    if c.type == "type_annotation":
                        tn = c
                ident = p.child_by_field_name("pattern") or (p.named_children[0] if p.named_children else None)
                if tn is not None and ident is not None and ident.type == "identifier":
                    tt = re.sub(r"[:\s]", "", self.text(tn)).split("<")[0].split("|")[0]
                    if tt and tt[0].isalpha():
                        sym.local_types[self.text(ident)] = tt
                        if any(c.type == "accessibility_modifier" for c in p.children) and ctx["cls"] is not None:
                            ctx["cls"].attr_types[self.text(ident)] = tt          # TS parameter properties
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body, {"cls": ctx["cls"], "fn": sym, "qual": sym.qualname})

    def visit_class(self, node, ctx):
        n = node.child_by_field_name("name")
        name = self.text(n) if n is not None else "<anonymous>"
        bases, impl = [], []
        for c in node.children:
            if c.type == "class_heritage":
                for h in c.named_children:
                    if h.type == "extends_clause":
                        bases += [self.text(x) for x in h.named_children if x.type in ("identifier", "member_expression", "type_identifier")]
                    elif h.type == "implements_clause":
                        impl += [self.text(x) for x in h.named_children if x.type in ("type_identifier", "identifier", "generic_type")]
                    elif h.type in ("identifier", "member_expression"):
                        bases.append(self.text(h))
        sym = self.add_symbol("class", name, node, ctx, bases=bases, implements=impl,
                              signature=f"class {name}" + (f" extends {', '.join(bases)}" if bases else ""))
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body, {"cls": sym, "fn": None, "qual": sym.qualname})

    def visit_import(self, node):
        src = node.child_by_field_name("source")
        if src is None:
            return
        rec = ImportRec(module=self.text(src).strip("'\"`"), line=node.start_point[0] + 1, kind="js")
        for c in node.named_children:
            if c.type == "import_clause":
                for x in c.named_children:
                    if x.type == "identifier":
                        rec.names.append(["default", self.text(x)])
                    elif x.type == "namespace_import":
                        ids = [y for y in x.named_children if y.type == "identifier"]
                        if ids:
                            rec.alias = self.text(ids[0])
                    elif x.type == "named_imports":
                        for sp in x.named_children:
                            nm, al = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                            rec.names.append([self.text(nm), self.text(al) if al is not None else None])
        self.imports.append(rec)

    def visit_call(self, node, ctx) -> bool:
        fn = node.child_by_field_name("function")
        text = self.callee(fn)
        args = node.child_by_field_name("arguments")
        owner = ctx["fn"].id if ctx["fn"] else self.file_id
        if text is None:
            return False
        if text == "require":
            return False
        a = args.named_children if args is not None else []
        str_arg = self.text(a[0]).strip("'\"`")[:120] if a and a[0].type in ("string", "template_string") else None
        flags = []
        if a and a[0].type == "template_string" and "${" in self.text(a[0]):
            flags.append("dynamic_arg")
        elif a and a[0].type == "binary_expression":
            flags.append("dynamic_arg")
        last = fn.child_by_field_name("property") if fn.type == "member_expression" else fn
        self.calls.append(CallRec(caller=owner, text=text, line=node.start_point[0] + 1, col=(last or fn).start_point[1],
                                  str_arg=str_arg, flags=flags))
        for e in detect_effects(text, JS_EFFECTS):
            if ctx["fn"] is not None:
                if e not in ctx["fn"].effects:
                    ctx["fn"].effects.append(e)
            else:
                self.module_effects.add(e)
        # Express-style route registration: app.get('/x', handler)  /  router.post('/x', (req,res)=>{...})
        if fn.type == "member_expression" and text.split(".")[-1] in _VERBS and str_arg and str_arg.startswith("/") and len(a) >= 2:
            verb = text.split(".")[-1].upper()
            h = a[-1]
            if h.type in _FUNC:
                sym = self.add_symbol("function", f"{verb} {str_arg}", h, ctx, signature=f"{verb} {str_arg}")
                sym.route = {"method": verb, "path": str_arg}
                body = h.child_by_field_name("body")
                if body is not None:
                    self.visit(body, {"cls": ctx["cls"], "fn": sym, "qual": sym.qualname})
                for x in a[:-1]:
                    self.visit(x, ctx)
                return True
            if h.type in ("identifier", "member_expression"):
                self.calls.append(CallRec(caller=owner, text=self.text(h), line=node.start_point[0] + 1, flags=["route", f"{verb} {str_arg}"]))
        return False


def parse_js(path: str, src: bytes) -> ParseResult:
    ext = path.rsplit(".", 1)[-1].lower()
    lang = "typescript" if ext in ("ts", "tsx") else "javascript"
    res = ParseResult(path=path, language=lang, is_test=is_test_path(path))
    res.loc = src.count(b"\n") + 1
    try:
        L = _TSX if ext == "tsx" else _TS if ext == "ts" else _JS
        tree = Parser(L).parse(src)
        w = _Walker(path, src)
        w.visit(tree.root_node, {"cls": None, "fn": None, "qual": ""})
        res.symbols, res.imports, res.calls = w.symbols, w.imports, w.calls
        res.effects, res.module_types = sorted(w.module_effects), w.module_types
        res.module_vars = sorted(set(w.module_vars))
        if tree.root_node.has_error:
            res.error = "syntax errors present (partial parse)"
    except Exception as e:
        res.error = f"parse failure: {e}"
    res.todos = scan_todos(src.decode("utf8", "replace"))
    return res
