"""Tree-sitter based Java parser (classes, interfaces, methods, constructors, imports, calls, JUnit tests)."""
from __future__ import annotations
import re
from tree_sitter import Language, Parser
import tree_sitter_java as tsjava

from .common import (
    CallRec, ImportRec, ParseResult, Symbol, detect_effects, is_test_path, scan_todos
)

_JAVA = Language(tsjava.language())
_COMPLEX = {
    "if_statement", "for_statement", "enhanced_for_statement", "while_statement",
    "do_statement", "switch_block_statement_group", "catch_clause", "ternary_expression"
}

JAVA_EFFECTS = [
    ("db", re.compile(r"(\b(Connection|PreparedStatement|Statement|ResultSet|EntityManager|JpaRepository)\b|\.(executeQuery|executeUpdate|select|insert|update|delete)\b)")),
    ("network", re.compile(r"(\b(HttpClient|HttpURLConnection|RestTemplate|WebClient|Socket)\b|\.(send|exchange|connect)\b)")),
    ("fs", re.compile(r"(\b(File|Path|Files|FileInputStream|FileOutputStream|BufferedReader|BufferedWriter)\b|\.(read|write|delete|createFile)\b)")),
    ("process", re.compile(r"(\b(ProcessBuilder|Runtime)\b|\.(exec|start)\b)")),
    ("eval", re.compile(r"(\b(ScriptEngine|Method\.invoke)\b)")),
]


class _JavaWalker:
    def __init__(self, path: str, src: bytes):
        self.path, self.src = path, src
        self.is_test_file = is_test_path(path) or path.endswith("Test.java") or path.endswith("Tests.java")
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
            if x.type in ("method_declaration", "constructor_declaration", "class_declaration"):
                continue
            stack.extend(x.children)
        return n

    def walk(self, node):
        for child in node.children:
            if child.type == "package_declaration":
                pkg_id = child.child_by_field_name("name") or [c for c in child.children if c.type in ("scoped_identifier", "identifier")]
                if pkg_id:
                    elem = pkg_id[0] if isinstance(pkg_id, list) else pkg_id
                    self.package_name = self.text(elem).strip()
            elif child.type == "import_declaration":
                self._handle_import(child)
            elif child.type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
                self._handle_class(child)
            elif child.type in ("method_declaration", "constructor_declaration"):
                self._handle_method(child, parent_qual=None, parent_id=None)

    def _handle_import(self, node):
        full_text = self.text(node).strip()
        # e.g., import java.util.List; or import static org.junit.Assert.*;
        is_static = "static " in full_text
        path_nodes = [c for c in node.children if c.type in ("scoped_identifier", "identifier")]
        if not path_nodes:
            return
        imp_path = self.text(path_nodes[0]).strip()
        parts = imp_path.split(".")
        mod = ".".join(parts[:-1]) if len(parts) > 1 else imp_path
        name = parts[-1] if len(parts) > 1 else "*"
        self.imports.append(ImportRec(
            module=mod,
            names=[[name, None]],
            line=node.start_point[0] + 1,
            kind="java_static" if is_static else "java"
        ))

    def _handle_class(self, node, parent_qual=None, parent_id=None):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        cname = self.text(name_node).strip()
        qual = f"{parent_qual}.{cname}" if parent_qual else cname
        cid = self.sid(qual)

        # Base classes and interfaces
        bases, interfaces = [], []
        superclass = node.child_by_field_name("superclass")
        if superclass:
            bases.append(self.text(superclass).replace("extends", "").strip())
        interfaces_node = node.child_by_field_name("interfaces")
        if interfaces_node:
            text_if = self.text(interfaces_node).replace("implements", "").strip()
            interfaces.extend([x.strip() for x in text_if.split(",") if x.strip()])

        is_test = self.is_test_file or cname.endswith("Test") or cname.endswith("Tests")
        body = node.child_by_field_name("body")

        sym = Symbol(
            id=cid,
            kind="class",
            name=cname,
            qualname=qual,
            path=self.path,
            start=node.start_point[0] + 1,
            end=node.end_point[0] + 1,
            parent=parent_id,
            bases=bases,
            implements=interfaces,
            is_test=is_test,
            loc=max(1, node.end_point[0] - node.start_point[0] + 1),
        )
        self.symbols.append(sym)

        if body:
            for member in body.children:
                if member.type in ("method_declaration", "constructor_declaration"):
                    self._handle_method(member, parent_qual=qual, parent_id=cid, class_is_test=is_test)
                elif member.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                    self._handle_class(member, parent_qual=qual, parent_id=cid)

    def _handle_method(self, node, parent_qual=None, parent_id=None, class_is_test=False):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        mname = self.text(name_node).strip()
        qual = f"{parent_qual}.{mname}" if parent_qual else mname
        mid = self.sid(qual)

        # Annotations check
        annotations = []
        for c in node.children:
            if c.type in ("marker_annotation", "annotation"):
                annotations.append(self.text(c).strip())

        is_test = class_is_test or any("Test" in a for a in annotations) or mname.startswith("test")
        params = node.child_by_field_name("parameters")
        sig = f"{mname}({self.text(params)[1:-1] if params else ''})"

        body = node.child_by_field_name("body")
        comp = self.complexity(body) if body else 1

        sym = Symbol(
            id=mid,
            kind="test" if is_test else ("method" if parent_id else "function"),
            name=mname,
            qualname=qual,
            path=self.path,
            start=node.start_point[0] + 1,
            end=node.end_point[0] + 1,
            parent=parent_id,
            signature=sig,
            decorators=annotations,
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
            if cur.type == "method_invocation":
                name_node = cur.child_by_field_name("name")
                if name_node:
                    call_name = self.text(name_node).strip()
                    obj_node = cur.child_by_field_name("object")
                    obj_text = self.text(obj_node).strip() if obj_node else None
                    full_call = f"{obj_text}.{call_name}" if obj_text else call_name
                    self.calls.append(CallRec(
                        caller=caller_id,
                        text=full_call,
                        line=cur.start_point[0] + 1,
                        col=cur.start_point[1],
                    ))
                    # check effects
                    effs = detect_effects(full_call, JAVA_EFFECTS)
                    for e in effs:
                        self.module_effects.add(e)
            elif cur.type in ("method_declaration", "constructor_declaration", "class_declaration"):
                continue  # don't dive into inner method definitions from outer caller
            stack.extend(cur.children)


def parse_java(path: str, src: bytes) -> ParseResult:
    """Parse Java source file using tree-sitter-java."""
    parser = Parser(_JAVA)
    tree = parser.parse(src)
    src_text = src.decode("utf8", "replace")

    walker = _JavaWalker(path, src)
    walker.walk(tree.root_node)

    loc = max(1, len(src_text.splitlines()))
    todos = scan_todos(src_text)

    return ParseResult(
        path=path,
        language="java",
        loc=loc,
        is_test=walker.is_test_file,
        symbols=walker.symbols,
        imports=walker.imports,
        calls=walker.calls,
        todos=todos,
        effects=sorted(list(walker.module_effects)),
    )

