from .common import ParseResult, Symbol, ImportRec, CallRec, is_test_path
from .python_parser import parse_python
from .js_parser import parse_js
from .java_parser import parse_java
from .go_parser import parse_go

LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
}


def parse_file(path: str, src: bytes) -> ParseResult:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext == ".py":
        return parse_python(path, src)
    if ext == ".java":
        return parse_java(path, src)
    if ext == ".go":
        return parse_go(path, src)
    return parse_js(path, src)
