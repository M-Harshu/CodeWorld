"""Language-neutral parse result structures."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Symbol:
    id: str
    kind: str                      # class | function | method | test
    name: str
    qualname: str
    path: str
    start: int
    end: int
    parent: str | None = None      # id of enclosing class/function symbol
    signature: str = ""
    doc: str = ""
    decorators: list[str] = field(default_factory=list)
    complexity: int = 1
    loc: int = 0
    bases: list[str] = field(default_factory=list)
    local_types: dict[str, str] = field(default_factory=dict)
    attr_types: dict[str, str] = field(default_factory=dict)
    effects: list[str] = field(default_factory=list)
    route: dict[str, Any] | None = None
    is_test: bool = False
    implements: list[str] = field(default_factory=list)


@dataclass
class ImportRec:
    module: str
    names: list[list[str | None]] = field(default_factory=list)   # [[name, alias], ...]
    alias: str | None = None
    level: int = 0
    line: int = 0
    kind: str = "import"      # import | from | js


@dataclass
class CallRec:
    caller: str               # symbol id, or "file:<path>" for module-level code
    text: str
    line: int
    col: int = 0
    str_arg: str | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    path: str
    language: str
    loc: int = 0
    is_test: bool = False
    doc: str = ""
    has_main: bool = False
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRec] = field(default_factory=list)
    calls: list[CallRec] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)       # Django-style url registrations
    todos: list[dict] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)       # module-level effects
    module_types: dict[str, str] = field(default_factory=dict)   # module-level `x = Foo()` hints
    module_vars: list[str] = field(default_factory=list)         # module-level variables/constants (for import checking)
    all_names: list[str] = field(default_factory=list)           # names listed in __all__ (public API)
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "ParseResult":
        d = json.loads(s)
        d["symbols"] = [Symbol(**x) for x in d["symbols"]]
        d["imports"] = [ImportRec(**x) for x in d["imports"]]
        d["calls"] = [CallRec(**x) for x in d["calls"]]
        return ParseResult(**d)


_TODO = re.compile(r"(?:#|//)\s*(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)")


def scan_todos(src: str) -> list[dict]:
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        m = _TODO.search(line)
        if m:
            out.append({"line": i, "tag": m.group(1), "text": m.group(2).strip()[:160]})
    return out[:50]


# ---- effect (side-effect capability) detection used for ACCESSES edges and the security map -----
PY_EFFECTS = [
    ("db", re.compile(r"(^|\.)(execute|executemany|executescript|cursor|fetchall|fetchone|fetchmany)$|^sqlite3\.|^psycopg2?\.|^pymysql\.|^asyncpg\.|\bsession\.(query|execute|add|commit)$|\.objects\.|^create_engine$|^sessionmaker$")),
    ("network", re.compile(r"^(requests|httpx|aiohttp|urllib3?)\.|(^|\.)urlopen$|^urllib\.request\.")),
    ("fs", re.compile(r"^(open|os\.(remove|unlink|rename|makedirs|mkdir|rmdir)|shutil\.\w+)$|\.(write_text|write_bytes|read_text|read_bytes|unlink|mkdir)$")),
    ("process", re.compile(r"^(subprocess\.\w+|os\.(system|popen|exec\w*|spawn\w*))$")),
    ("eval", re.compile(r"^(eval|exec)$|^builtins\.(eval|exec)$")),
    ("deserialize", re.compile(r"^(pickle|cPickle|marshal|dill|shelve)\.loads?$|^yaml\.(load|unsafe_load)$|^jsonpickle\.decode$")),
    ("weak-crypto", re.compile(r"^hashlib\.(md5|sha1)$")),
]
JS_EFFECTS = [
    ("db", re.compile(r"\.(query|execute|raw)$|^(mongoose|prisma|knex|sequelize)\.")),
    ("network", re.compile(r"^(fetch|axios(\.\w+)?|got|superagent)$|^(http|https)\.(get|request)$")),
    ("fs", re.compile(r"^fs(\.promises)?\.\w+$")),
    ("process", re.compile(r"^(exec|execSync|spawn|spawnSync)$|^child_process\.\w+$")),
    ("eval", re.compile(r"^(eval|Function)$")),
    ("deserialize", re.compile(r"^(unserialize|deserialize)$|\.unserialize$")),
]


def detect_effects(text: str, patterns) -> list[str]:
    return [name for name, rx in patterns if rx.search(text)]


def is_test_path(path: str) -> bool:
    p = path.lower().replace("\\", "/")   # normalise Windows separators
    base = p.rsplit("/", 1)[-1]
    parts = p.split("/")
    return (
        base.startswith("test_") or base.endswith("_test.py") or base == "conftest.py"
        or ".test." in base or ".spec." in base
        or any(x in ("tests", "test", "__tests__", "spec") for x in parts[:-1])
    )
