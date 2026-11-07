"""Independent AI-PR guardrail benchmark: evaluate realistic AI-agent vs human PRs across severity thresholds."""
from __future__ import annotations
from typing import Any
from ..analysis.diffreview import review_diff


def get_benchmark_prs() -> list[dict[str, Any]]:
    """Return an independent benchmark dataset of AI-authored and human-authored PR diffs."""
    return [
        {
            "id": "ai-pr-01-hallucinated-package",
            "author": "ai_agent_claude",
            "has_defect": True,
            "defect_type": "hallucinated_package",
            "patch": """--- a/utils.py
+++ b/utils.py
@@ -1,3 +1,4 @@
+import fast_crypto_accelerator_v2
 def compute_hash(data):
-    return data.encode()
+    return fast_crypto_accelerator_v2.hash(data)
""",
        },
        {
            "id": "ai-pr-02-dangling-reference",
            "author": "ai_agent_copilot",
            "has_defect": True,
            "defect_type": "dangling_reference",
            "patch": """--- a/service.py
+++ b/service.py
@@ -10,3 +10,4 @@
 def process_order(order):
-    return True
+    return validate_order_item_schema(order)
""",
        },
        {
            "id": "ai-pr-03-doc-drift",
            "author": "ai_agent_chatgpt",
            "has_defect": True,
            "defect_type": "doc_drift",
            "patch": """--- a/client.py
+++ b/client.py
@@ -5,4 +5,0 @@
-def execute_transaction():
-    pass
--- a/README.md
+++ b/README.md
@@ -10,2 +10,2 @@
-Call `execute_transaction()` to start.
+Call `execute_transaction()` to start.
""",
        },
        {
            "id": "ai-pr-04-clean-feature",
            "author": "ai_agent_cursor",
            "has_defect": False,
            "defect_type": None,
            "patch": """--- a/math_utils.py
+++ b/math_utils.py
@@ -5,3 +5,6 @@
 def add(a, b):
     return a + b
+
+def multiply(a, b):
+    return a * b
""",
        },
        {
            "id": "human-pr-01-clean-refactor",
            "author": "human_contributor",
            "has_defect": False,
            "defect_type": None,
            "patch": """--- a/config.py
+++ b/config.py
@@ -1,4 +1,4 @@
-TIMEOUT = 30
+TIMEOUT = 60
""",
        },
        {
            "id": "human-pr-02-bugfix-with-tests",
            "author": "human_contributor",
            "has_defect": False,
            "defect_type": None,
            "patch": """--- a/parser.py
+++ b/parser.py
@@ -8,3 +8,3 @@
-    if x is None:
+    if x is None or not x:
         return False
--- a/test_parser.py
+++ b/test_parser.py
@@ -15,2 +15,4 @@
+def test_empty_string():
+    assert not parser.parse("")
""",
        },
    ]


def evaluate_independent_guardrail(ctx) -> dict[str, Any]:
    """Evaluate guardrail across independent AI and human PRs at multiple severity thresholds."""
    dataset = get_benchmark_prs()

    thresholds = ["high", "medium", "low"]
    results_by_thresh = {}

    for thresh in thresholds:
        tp, fp, tn, fn = 0, 0, 0, 0
        min_severities = {"high": ["high"], "medium": ["high", "medium"], "low": ["high", "medium", "low"]}[thresh]

        for item in dataset:
            report = review_diff(ctx, item["patch"])
            triggered = any(f.get("severity") in min_severities for f in report.get("findings", []))

            if item["has_defect"]:
                if triggered:
                    tp += 1
                else:
                    fn += 1
            else:
                if triggered:
                    fp += 1
                else:
                    tn += 1

        precision = tp / max(1, (tp + fp))
        recall = tp / max(1, (tp + fn))
        f1 = (2 * precision * recall) / max(0.001, (precision + recall))

        results_by_thresh[thresh] = {
            "threshold": thresh,
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "false_positive_rate": round(fp / max(1, (fp + tn)), 3),
        }

    return {
        "total_prs": len(dataset),
        "ai_prs": len([p for p in dataset if "ai" in p["author"]]),
        "human_prs": len([p for p in dataset if "human" in p["author"]]),
        "threshold_curve": results_by_thresh,
    }

