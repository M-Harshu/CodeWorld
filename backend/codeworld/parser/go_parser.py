"""Tree-sitter based Go parser (packages, structs, interfaces, functions, methods, imports, calls, tests)."""
from __future__ import annotations
import re
from tree_sitter import Language, Parser
import tree_sitter_go as tsgo

from .common import (
    CallRec, ImportRec, ParseResult, Symbol, detect_effects, is_test_path, scan_todos
)

_GO = Language(tsgo.language())
_COMPLEX = {
    "if_statement", "for_statement", "type_switch_statement", "expression_switch_statement",
    "select_statement", "communication_case", "expression_case"
}

GO_EFFECTS = [
    ("db", re.compile(r"(\b(sql\.Open|sqlx\.Open|gorm\.Open|pgx\.)\b|\.(Query|Exec|QueryRow|Begin|Commit)\b)")),
    ("network", re.compile(r"(\b(http\.Get|http\.Post|http\.Client|net\.Dial|grpc\.Dial)\b|\.(Do|Get|Post)\b)")),
    ("fs", re.compile(r"(\b(os\.Open|os\.Create|os\.ReadFile|os\.WriteFile|ioutil\.ReadFile|filepath\.)\b)")),
    ("process", re.compile(r"(\b(exec\.Command|exec\.CommandContext|os\.StartProcess)\b|\.(Run|Output|CombinedOutput)\b)")),
    ("eval", re.compile(r"(\b(reflect\.ValueOf|plugin\.Open)\b)")),
]


class _GoWalker:
    def __init__(self, path: str, src: bytes):
        self.path, self.src = path, src
        self.is_test_file = is_test_path(path) or path.endswith("_test.go")
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRec] = []
        self.calls: list[CallRec] = []
        self.module_effects: set[str] = set()
        self.package_name: str = ""
        self.file_id = f"file:{path}"

    def text(self, n) -> str:
        if n is None:
            return ""
        return self.src[n.start_byte:n.end_byte].decode("utf8", "replace")

    def sid(self, q: str) -> str:
        return f"sym:{self.path}::{q}"

    def complexity(self, node) -> int:
        n, stack = 1, list(node.children)
        while stack:
            x = stack.pop()
            if x.type in _COMPLEX:
                n += 1
            elif x.type == "binary_expression":
                op = x.child_by_field_name("operator")
                if op is not None and self.text(op) in ("&&", "||"):
                    n += 1
            if x.type in ("function_declaration", "method_declaration"):
                continue
            stack.extend(x.children)
        return n

    def walk(self, node):
        for child in node.children:
            if child.type == "package_clause":
                pkg_id = child.child_by_field_name("package") or [c for c in child.children if c.type == "package_identifier"]
                if pkg_id:
                    elem = pkg_id[0] if isinstance(pkg_id, list) else pkg_id
                    self.package_name = self.text(elem).strip()
            elif child.type == "import_declaration":
                self._handle_import(child)
            elif child.type == "type_declaration":
                self._handle_type(child)
            elif child.type == "function_declaration":
                self._handle_function(child)
            elif child.type == "method_declaration":
                self._handle_method(child)

    def _handle_import(self, node):
        # Can have import_spec or import_spec_list
        for c in node.children:
            if c.type == "import_spec":
                self._add_import_spec(c)
            elif c.type == "import_spec_list":
                for spec in c.children:
                    if spec.type == "import_spec":
                        self._add_import_spec(spec)

    def _add_import_spec(self, spec_node):
        path_node = spec_node.child_by_field_name("path")
        if not path_node:
            return
        raw_path = self.text(path_node).strip().strip('"')
        alias_node = spec_node.child_by_field_name("name")
        alias = self.text(alias_node).strip() if alias_node else None

        self.imports.append(ImportRec(
            module=raw_path,
            names=[[raw_path.split("/")[-1], alias]],
            alias=alias,
            line=spec_node.start_point[0] + 1,
            kind="go"
        ))

    def _handle_type(self, node):
        for spec in node.children:
            if spec.type == "type_spec":
                name_node = spec.child_by_field_name("name")
                type_node = spec.child_by_field_name("type")
                if not name_node:
                    continue
                tname = self.text(name_node).strip()
                tid = self.sid(tname)
                kind = "class" if type_node and type_node.type in ("struct_type", "interface_type") else "type"

                sym = Symbol(
                    id=tid,
                    kind=kind,
                    name=tname,
                    qualname=tname,
                    path=self.path,
                    start=spec.start_point[0] + 1,
                    end=spec.end_point[0] + 1,
                    loc=max(1, spec.end_point[0] - spec.start_point[0] + 1),
                )
                self.symbols.append(sym)

    def _handle_function(self, node):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        fname = self.text(name_node).strip()
        fid = self.sid(fname)
        is_test = self.is_test_file or fname.startswith("Test") or fname.startswith("Benchmark")
        params = node.child_by_field_name("parameters")
        sig = f"{fname}({self.text(params)[1:-1] if params else ''})"

        body = node.child_by_field_name("body")
        comp = self.complexity(body) if body else 1

        sym = Symbol(
            id=fid,
            kind="test" if is_test else "function",
            name=fname,
            qualname=fname,
            path=self.path,
            start=node.start_point[0] + 1,
            end=node.end_point[0] + 1,
            signature=sig,
            complexity=comp,
            is_test=is_test,
            loc=max(1, node.end_point[0] - node.start_point[0] + 1),
        )
        self.symbols.append(sym)

        if body:
            self._find_calls(body, caller_id=fid)

    def _handle_method(self, node):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        mname = self.text(name_node).strip()
        receiver_node = node.child_by_field_name("receiver")
        rec_type = ""
        if receiver_node:
            rec_text = self.text(receiver_node).strip("()")
            # extract type name e.g. "s *Server" -> "Server"
            parts = rec_text.split()
            if parts:
                rec_type = parts[-1].lstrip("*")

        qual = f"{rec_type}.{mname}" if rec_type else mname
        mid = self.sid(qual)
        params = node.child_by_field_name("parameters")
        sig = f"({rec_type}) {mname}({self.text(params)[1:-1] if params else ''})"

        body = node.child_by_field_name("body")
        comp = self.complexity(body) if body else 1
        is_test = self.is_test_file or mname.startswith("Test")

        sym = Symbol(
            id=mid,
            kind="test" if is_test else "method",
            name=mname,
            qualname=qual,
            path=self.path,
            start=node.start_point[0] + 1,
            end=node.end_point[0] + 1,
            signature=sig,
            complexity=comp,
            is_test=is_test,
            loc=max(1, node.end_point[0] - node.start_point[0] + 1),
        )
        self.symbols.append(sym)

        if body:
            self._find_calls(body, caller_id=mid)

    def _find_calls(self, node, caller_id: str):
        stack = list(node.children)
        while stack:
            cur = stack.pop()
            if cur.type == "call_expression":
                fn_node = cur.child_by_field_name("function")
                if fn_node:
                    call_text = self.text(fn_node).strip()
                    self.calls.append(CallRec(
                        caller=caller_id,
                        text=call_text,
                        line=cur.start_point[0] + 1,
                        col=cur.start_point[1],
                    ))
                    effs = detect_effects(call_text, GO_EFFECTS)
                    for e in effs:
                        self.module_effects.add(e)
            elif cur.type in ("function_declaration", "method_declaration"):
                continue
            stack.extend(cur.children)


def parse_go(path: str, src: bytes) -> ParseResult:
    """Parse Go source file using tree-sitter-go."""
    parser = Parser(_GO)
    tree = parser.parse(src)
    src_text = src.decode("utf8", "replace")

    walker = _GoWalker(path, src)
    walker.walk(tree.root_node)

    loc = max(1, len(src_text.splitlines()))
    todos = scan_todos(src_text)

    return ParseResult(
        path=path,
        language="go",
        loc=loc,
        is_test=walker.is_test_file,
        symbols=walker.symbols,
        imports=walker.imports,
        calls=walker.calls,
        todos=todos,
        effects=sorted(list(walker.module_effects)),
    )

