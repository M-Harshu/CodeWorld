from codeworld.analysis import health, impact, onboarding, security, importcheck
from codeworld.agent.answer import ask
from codeworld.agent.tools import ToolRegistry


def test_impact_includes_endpoint_and_tests(ctx):
    r = impact.impact(ctx, "verify_password")
    assert r["apis"] and r["tests"] and r["risk"]["score"] > 0
    assert any(d["name"] == "AuthService.login" for d in r["direct"])


def test_test_selection(ctx):
    names = {t["name"] for t in impact.tests_for(ctx, "AuthService.register")["tests"]}
    assert "test_register_and_login" in names


def test_health_findings(ctx):
    h = health.health_report(ctx)
    assert any("order_repository" in v["from"] for v in h["layering"])
    assert {d["name"] for d in h["dead_code"]} >= {"unused_helper", "eval_formula"}
    assert any({d["a"]["name"], d["b"]["name"]} == {"total_price_a", "total_price_b"} for d in h["duplicates"])
    assert 0 <= h["score"] <= 100


def test_security_surface(ctx):
    s = security.security_report(ctx)
    kinds = {f["kind"] for f in s["findings"]}
    assert {"eval", "process"} <= kinds
    assert any(f["severity"] == "high" and "SQL" in f["message"] for f in s["findings"])
    assert any(a["api"] == "GET /report/{name}" for a in s["attack_surface"])


def test_onboarding(ctx):
    o = onboarding.overview(ctx)
    assert o["flows"] and "flowchart" in o["flows"][0]["mermaid"]
    md = onboarding.markdown(ctx)
    assert "```mermaid" in md and "Glossary" in md


def test_offline_answers_are_grounded(ctx):
    r = ask(ctx, "Who calls hash_password?")
    assert "AuthService.register" in r["answer"] and r["citation_precision"] == 1.0
    r = ask(ctx, "what breaks if I change verify_password?")
    assert "POST /login" in r["answer"]


def test_tools_registry(ctx):
    t = ToolRegistry(ctx)
    assert len(t.describe()) >= 14
    assert t.call("find_symbol", {"name": "AuthService"})["matches"]
    flow = t.call("explain_flow", {"entry": "POST /login"})
    assert any(s["name"] == "AuthService.login" for s in flow["steps"])


def test_import_checker_flags_invented_names(ctx):
    # existing fixture has valid imports only -> nothing high severity
    assert not [f for f in importcheck.check_imports(ctx) if f["severity"] == "high"]
