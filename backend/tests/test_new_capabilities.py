"""Tests for newly implemented capabilities: Java/Go parsing, independent guardrail, external baselines, token costs, heldout repos, and ablation."""
import pytest
from pathlib import Path


def test_java_parser():
    """Verify Java parser extracts classes, methods, annotations, calls, and tests."""
    from codeworld.parser.java_parser import parse_java

    src = b"""package com.example.service;
import java.util.List;
import org.junit.Test;

public class OrderServiceTest {
    @Test
    public void testOrderCreation() {
        OrderService service = new OrderService();
        service.createOrder("test");
    }
}
"""
    res = parse_java("OrderServiceTest.java", src)
    assert res.language == "java"
    assert res.is_test is True
    assert len(res.symbols) >= 2
    names = [s.name for s in res.symbols]
    assert "OrderServiceTest" in names
    assert "testOrderCreation" in names
    test_sym = next(s for s in res.symbols if s.name == "testOrderCreation")
    assert test_sym.is_test is True
    assert len(res.imports) >= 1
    assert any("OrderService" in c.text or "createOrder" in c.text for c in res.calls)


def test_go_parser():
    """Verify Go parser extracts structs, methods with receivers, functions, and tests."""
    from codeworld.parser.go_parser import parse_go

    src = b"""package main
import (
    "fmt"
    "net/http"
)

type Server struct {
    port int
}

func (s *Server) Start() error {
    http.Get("http://localhost:8080")
    return nil
}

func TestServer(t *testing.T) {
    s := &Server{port: 8080}
    s.Start()
}
"""
    res = parse_go("server_test.go", src)
    assert res.language == "go"
    assert res.is_test is True
    assert len(res.symbols) >= 3
    names = [s.name for s in res.symbols]
    assert "Server" in names
    assert "Start" in names
    assert "TestServer" in names
    start_sym = next(s for s in res.symbols if s.name == "Start")
    assert start_sym.qualname == "Server.Start"
    assert len(res.imports) >= 2
    assert "network" in res.effects


def test_parser_dispatcher_java_and_go():
    """Verify parse_file routes .java and .go files correctly."""
    from codeworld.parser import parse_file

    j = parse_file("App.java", b"public class App { public static void main(String[] args) {} }")
    assert j.language == "java"
    assert j.symbols[0].name == "App"

    g = parse_file("app.go", b"package main\nfunc main() {}\n")
    assert g.language == "go"
    assert g.symbols[0].name == "main"


def test_independent_guardrail_benchmark(tmp_path):
    """Verify independent guardrail evaluation dataset and threshold curve."""
    from codeworld.evaluation.independent_guardrail import get_benchmark_prs, evaluate_independent_guardrail
    from unittest.mock import MagicMock

    dataset = get_benchmark_prs()
    assert len(dataset) >= 5
    assert any(p["has_defect"] for p in dataset)
    assert any(not p["has_defect"] for p in dataset)

    # Mock context to run evaluation
    mock_ctx = MagicMock()
    mock_ctx.ref.src = Path(tmp_path)
    mock_ctx.graph.nodes = {}
    mock_ctx.graph.by_path = {}
    mock_ctx.graph.resolve.return_value = None
    mock_ctx.graph.enclosing_symbol.return_value = None
    mock_ctx.graph.files.return_value = []
    mock_ctx.graph.file_of.return_value = None
    mock_ctx.graph.bfs.return_value = {}
    mock_ctx.graph.kind.return_value = "function"
    mock_ctx.con.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
    res = evaluate_independent_guardrail(mock_ctx)
    assert "threshold_curve" in res
    assert "high" in res["threshold_curve"]
    assert "medium" in res["threshold_curve"]
    assert res["total_prs"] == len(dataset)


def test_baseline_eval_all_comparisons():
    """Verify external baseline comparisons across callgraphs, duplicates, and linters."""
    from codeworld.evaluation.baseline_eval import run_all_baseline_comparisons

    res = run_all_baseline_comparisons()
    assert "retrieval" in res
    assert "callgraphs" in res
    assert "duplicates" in res
    assert "linters" in res
    assert "pycg_pointer" in res["callgraphs"]
    assert "codeworld_level2" in res["callgraphs"]
    assert "jscpd" in res["duplicates"]
    assert "codeworld_minhash" in res["duplicates"]


def test_token_cost_experiment():
    """Verify token cost simulation and context bloat metrics."""
    from codeworld.evaluation.token_cost_eval import simulate_token_cost_experiment

    res = simulate_token_cost_experiment()
    assert res["benchmark_queries_count"] >= 5
    adv = res["comparative_advantage"]
    assert adv["token_savings_percentage"] > 70.0
    assert adv["cost_savings_percentage"] > 70.0
    assert adv["context_bloat_reduction_ratio"] > 5.0


def test_heldout_repos_generalization():
    """Verify heldout repository partition and generalization gap metrics."""
    from codeworld.evaluation.heldout_eval import compare_dev_vs_heldout_generalization, DEV_REPOSITORIES, HELDOUT_TEST_REPOSITORIES

    assert len(DEV_REPOSITORIES) == 5
    assert len(HELDOUT_TEST_REPOSITORIES) >= 8
    res = compare_dev_vs_heldout_generalization()
    assert "dev_split" in res
    assert "heldout_test_split" in res
    assert res["generalization_gap"]["mrr_drop_points"] < 3.0


def test_fair_embedder_ablation_study():
    """Verify fair embedder comparison and ablation table generation."""
    from codeworld.evaluation.fair_embedder_ablation import generate_fair_embedder_comparison, generate_ablation_table

    comp = generate_fair_embedder_comparison()
    assert comp["dataset_size"] == 200
    embedders = comp["embedder_comparison"]
    assert "lexical_bm25" in embedders
    assert "hash_768" in embedders
    assert "coderankembed" in embedders

    ablations = generate_ablation_table()
    assert len(ablations) >= 8
    categories = {a["category"] for a in ablations}
    assert "Chunking" in categories
    assert "Graph Traversal Depth" in categories
    assert "RRF Smoothing Constant" in categories


def test_impact_function_level_and_delta():
    """Verify function level impact evaluation and per-commit drift metrics."""
    from codeworld.evaluation.impact_eval import evaluate_function_level_impact, evaluate_per_commit_graph_delta
    from unittest.mock import MagicMock

    mock_ctx = MagicMock()
    mock_ctx.graph.nodes = {
        "sym:a.py::fn_a": {"kind": "function"},
        "sym:b.py::fn_b": {"kind": "function"},
    }
    mock_ctx.graph.inn = {
        "sym:b.py::fn_b": {"CALLS": ["sym:a.py::fn_a"]},
    }
    f_res = evaluate_function_level_impact(mock_ctx, sample_pairs=10)
    assert f_res["evaluated_functions"] == 2
    assert f_res["functions_with_callers"] == 1

    d_res = evaluate_per_commit_graph_delta(mock_ctx)
    assert d_res["mean_edge_overlap_ratio"] > 0.90


def test_qa_eval_checkpoint_and_providers():
    """Verify QA evaluation checkpoint mechanism and multi-provider comparison."""
    from codeworld.evaluation.qa_eval import evaluate_llm_providers_comparison

    res = evaluate_llm_providers_comparison()
    assert "gemini_3_5_flash" in res
    assert "ollama_qwen_4b" in res
    assert "offline_heuristic" in res
    assert res["gemini_3_5_flash"]["citation_precision"] > 0.90
