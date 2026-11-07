from pathlib import Path
from codeworld.parser import parse_file
from codeworld.graph.builder import GraphBuilder
from conftest import FIXTURE


def _parsed():
    return {str(p.relative_to(FIXTURE)): parse_file(str(p.relative_to(FIXTURE)), p.read_bytes())
            for p in FIXTURE.rglob("*") if p.suffix in (".py", ".js")}


def test_python_symbols_and_types():
    r = parse_file("app/services/auth_service.py", (FIXTURE / "app/services/auth_service.py").read_bytes())
    names = {s.qualname for s in r.symbols}
    assert {"AuthService", "AuthService.login", "AuthService.register"} <= names
    cls = next(s for s in r.symbols if s.qualname == "AuthService")
    assert cls.attr_types["users"] == "UserRepository"          # injected dependency typed from the annotation


def test_routes_and_di():
    r = parse_file("app/api/auth.py", (FIXTURE / "app/api/auth.py").read_bytes())
    login = next(s for s in r.symbols if s.name == "login")
    assert login.route == {"method": "POST", "path": "/login"}
    assert any("di" in c.flags and c.text == "deps.get_auth_service" for c in r.calls)


def test_js_parsing():
    r = parse_file("web/server.js", (FIXTURE / "web/server.js").read_bytes())
    assert {s.route["path"] for s in r.symbols if s.route} == {"/cart/:id", "/cart"}


def test_resolution_levels_improve_recall():
    parsed = _parsed()
    n2, e2, _ = GraphBuilder(parsed, level=2).build()
    n1, e1, _ = GraphBuilder(parsed, level=1).build()
    key = lambda e: {(s.split("::")[-1], d.split("::")[-1]) for (s, d, t) in e if t == "CALLS"}
    assert ("AuthService.login", "UserRepository.find_by_email") in key(e2)
    assert ("AuthService.login", "UserRepository.find_by_email") not in key(e1)    # needs type flow


def test_graph_edges_and_tests():
    n, e, _ = GraphBuilder(_parsed(), level=2).build()
    types = {t for (_, _, t) in e}
    assert {"CALLS", "IMPORTS", "CONTAINS", "EXPOSES", "TESTS", "ACCESSES"} <= types
    assert any(k[2] == "TESTS" and k[0].endswith("test_register_and_login") for k in e)
    assert any(k[0].endswith("login") and k[1] == "api:POST /login" and k[2] == "EXPOSES" for k in e)


def test_jedi_level_runs():
    parsed = _parsed()
    nodes, edges, _ = GraphBuilder(parsed, level=3, root=FIXTURE).build()
    assert sum(1 for k in edges if k[2] == "CALLS") >= 40
