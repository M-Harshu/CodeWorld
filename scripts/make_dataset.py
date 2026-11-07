"""Generate a QA evaluation dataset for a repository using CodeWorld's autoqa generator.

Usage:
  python scripts/make_dataset.py <repo_id> [--output path/to/dataset.json] [--n 50]
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from codeworld.config import get_settings
from codeworld.service import get_context
from codeworld.evaluation import autoqa


def generate_dataset(repo_id: str, n: int = 50, output_path: str | None = None) -> list[dict]:
    ctx = get_context(repo_id)
    if not ctx:
        print(f"Error: repository '{repo_id}' not found or not indexed.", file=sys.stderr)
        sys.exit(1)

    print(f"Generating {n} doc2code benchmark questions for {ctx.ref.name}...")
    dataset = autoqa.doc2code(ctx, n=n)

    out_file = Path(output_path) if output_path else Path(f"{ctx.ref.name}_qa.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    print(f"Saved {len(dataset)} benchmark questions to {out_file}")
    return dataset


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate QA dataset for an indexed repo")
    parser.add_argument("repo_id", help="Repository ID in CodeWorld (e.g. click-ee7fe6)")
    parser.add_argument("--n", type=int, default=50, help="Number of questions to generate (default 50)")
    parser.add_argument("--output", "-o", help="Output JSON path")
    args = parser.parse_args()

    generate_dataset(args.repo_id, n=args.n, output_path=args.output)

