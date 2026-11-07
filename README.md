# CodeWorld

[![CI](https://github.com/Kavya-Garg003/codeworld/actions/workflows/ci.yml/badge.svg)](https://github.com/Kavya-Garg003/codeworld/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10 | 3.11 | 3.12](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-brightgreen.svg)](https://www.python.org/)
[![Platform: Linux | macOS | Windows](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)](SETUP_AND_RUN.md)

**A local-first code knowledge graph, impact analysis engine, and AI pull-request guardrail.**

CodeWorld parses multi-language repositories into a typed code property graph, answers architecture and implementation questions with strictly verified citations, predicts which files and tests are affected by a change, and audits pull request diffs for common generative AI mistakes. It runs entirely on commodity CPU hardware, requires no external database daemon, and never executes untrusted code during analysis.

---

## Why CodeWorld?

Modern coding assistants and LLMs are powerful, but when dropped into large multi-module repositories, standard text-based retrieval (flat RAG) reveals three critical weaknesses:

1. **Relational Blindness:** Code is a directed, typed graph, not flat text. Slicing source files into arbitrary token chunks obscures inter-procedural call chains, inheritance hierarchies, and import dependencies. When asked relational questions—such as *"Which CLI commands invoke function $f$?"* or *"What breaks if this signature changes?"*—flat vector similarity fails because callers and callees share little textual resemblance.
2. **Generative Hallucinations in PRs:** Coding agents frequently commit subtle, syntactically valid errors: hallucinated third-party dependencies, dangling method calls, duplicate utility functions, and stale documentation. Human reviewers routinely miss these in large diffs.
3. **Heavy Infrastructure & Cloud Dependency:** Existing code search tools typically rely on heavy enterprise graph databases (Neo4j, Dgraph, massive JVM heaps) or require streaming entire proprietary codebases to remote cloud endpoints.

**CodeWorld provides a lightweight, local alternative:**
- **Zero Heavy Infrastructure:** Runs in-process with SQLite and pure-Python graph algorithms on any developer laptop (Linux, macOS, Windows).
- **Verified Citations:** Every source citation `[path:line]` returned during Q&A is validated against the indexed code graph to prevent hallucinated line numbers.
- **Privacy First:** Analyzes repositories locally with no mandatory cloud API keys; supports fully offline local LLMs (via Ollama) and zero-download hashing embedders.

---

## Architecture Overview

```
                      ┌────────────────────────────────────────┐
                      │        Source Code Repository          │
                      │      (Python, JS/TS, Java, Go)         │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │        Tree-sitter AST Parsers         │
                      │  Symbols · Scopes · Calls · Imports    │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │      Hierarchical Call Resolvers       │
                      │  Level 0: Syntactic Name Matching      │
                      │  Level 1: Scope + Import Resolution    │
                      │  Level 2: Intra-Procedural Type Flow   │
                      │  Level 3: Jedi Semantic Analysis       │
                      │  Level 4: External LSP / Pyright       │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │       Code Property Graph (CPG)        │
                      │  SQLite FTS5 (BM25) + Vector Chunks    │
                      └───────┬────────────────────────┬───────┘
                              │                        │
             ┌────────────────┴──────┐  ┌──────────────┴────────────────┐
             ▼                       │  ▼                               ▼
┌──────────────────────────┐         │ ┌──────────────────┐ ┌──────────────────┐
│   Intent-Routed Search   │         │ │  Change Impact   │ │   PR Guardrail   │
│ BM25 + Vectors + 2-hop   │         │ │ Static + History │ │ Hallucinated Pkg │
│ Graph Walk (via RRF)     │         │ │  Test Selection  │ │  Dangling Refs   │
└────────────┬─────────────┘         │ └────────┬─────────┘ │  Doc-Drift Check │
             │                       │          │           └────────┬─────────┘
             ▼                       ▼          ▼                    ▼
┌──────────────────────────┐ ┌───────────────┐ ┌──────────┐ ┌──────────────────┐
│ Verified-Citation Q&A    │ │ MCP Server    │ │ Ready    │ │ SARIF 2.1.0 /    │
│ Multi-Provider (Gemini / │ │ (Cursor /     │ │ Test     │ │ PR Markdown      │
│ OpenRouter / Ollama)     │ │  Claude Code) │ │ Command  │ │ Report           │
└──────────────────────────┘ └───────────────┘ └──────────┘ └──────────────────┘
```

---

## Features

### 1. Deterministic AST Parsing & Graph Building
- **Multi-Language Support:** Deterministic Tree-sitter parsers for Python, JavaScript, TypeScript, Java, and Go.
- **Rich Entity Extraction:** Extracts classes, interfaces, structs, methods with receivers, decorators, HTTP route endpoints (FastAPI, Flask, Django, Express), and side-effects (database, filesystem, network, subprocess).
- **Tiered Call Resolution:** Resolves method calls through five configurable tiers: from syntactic name-matching (Level 0) up to lexical scopes (Level 1), intra-procedural type flow and MRO walks (Level 2, default), Jedi semantics (Level 3), and external LSP (Level 4).

### 2. Intent-Routed Hybrid Retrieval
- **Intent Classifier:** Detects whether a query is looking for a concept definition or traversing structural relationships.
- **Reciprocal Rank Fusion (RRF):** Fuses SQLite FTS5 (BM25) lexical search with dense vector embeddings (Hash-768 zero-download embedder or `nomic-ai/CodeRankEmbed`).
- **Graph Expansion:** Automatically expands context along directed call, inheritance, and import chains for structural queries.

### 3. Change Impact Analysis & Test Selection
- **Hybrid Ripple Analysis:** Combines static reverse-reachability on the knowledge graph with historical Git co-change mining to catch dependencies invisible in static syntax.
- **Automated Test Selection:** Automatically identifies affected test suites and outputs ready-to-run commands (e.g., `pytest tests/test_parser.py -q`), avoiding the need to run entire test suites on minor changes.

### 4. AI-PR Guardrail & Safety Suite
- **Hallucinated Package Verification:** Inspects added imports and verifies package existence against cached registries and live PyPI/npm HTTP endpoints.
- **Dangling Reference Detection:** Detects imports or calls to members that do not exist in the target module.
- **Broken Attribute Dispatches:** Flags method invocations on objects with known types where the attribute is absent.
- **Duplicate Helper Detection:** Identifies newly written utility functions that duplicate existing internal functions via MinHash token shingling.
- **Documentation Drift:** Scans README files and docstrings to find references to functions or classes deleted or modified in recent diffs.
- **SARIF 2.1.0 Export:** Directly integrates into GitHub Actions code scanning to display findings inline on PR diffs.

### 5. Multi-Provider LLM & Vision Resilience
- **Supported Providers:** Google Gemini (multimodal image/vision input), OpenRouter (supports reasoning token passthrough), and Ollama (local offline CPU models such as `qwen3.5:4b`).
- **Resilient Fallback:** Automatically switches across configured providers if rate limits or network issues occur.
- **Offline Mode:** Operates with a deterministic rule-based planner when no LLM is configured.

### 6. Developer Ecosystem & Tooling
- **Model Context Protocol (MCP) Server:** Stdio MCP server exposing 16 graph tools directly to Cursor, Claude Code, Copilot, and Windsurf.
- **Web UI:** React + React Flow + Three.js interface featuring interactive code maps, 3D code city visualizations, chat, impact explorer, and guardrail reports.
- **Continuous Watch Mode:** Automatically monitors files and incrementally updates the knowledge graph on save.
- **VS Code Extension:** Provides in-editor CodeLens (callers, tests, risk), inline problem diagnoses, and patch proposals.

---

## Quick Start

### Prerequisites
- **Python:** 3.10, 3.11, or 3.12
- **Node.js:** 18+ (only required if building the Web UI from source)
- **Git**

### Automated Setup

**Linux / macOS:**
```bash
git clone https://github.com/Kavya-Garg003/codeworld.git
cd codeworld
./scripts/setup.sh
./scripts/run.sh
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/Kavya-Garg003/codeworld.git
Set-Location codeworld
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```

Once running, navigate to `http://localhost:8000` in your web browser.

---

## Manual Installation

### 1. Backend Environment Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1

# Install dependencies
pip install -r backend/requirements.txt
```

### 2. Configure Environment (Optional)
Copy `.env.example` to `.env` to configure LLM keys or settings:
```bash
cp .env.example .env
```
Key configuration variables:
```ini
# LLM Providers (optional - CodeWorld works offline without them)
GEMINI_API_KEY=your_gemini_key
OPENROUTER_API_KEY=your_openrouter_key
OLLAMA_HOST=http://127.0.0.1:11434

# Embedding Backend: "hash" (default zero-download) or "st" (sentence-transformers / CodeRankEmbed)
CODEWORLD_EMBEDDINGS=hash

# Server settings
CODEWORLD_PORT=8000
CODEWORLD_ALLOW_LOCAL=1
```

### 3. Build Frontend UI
```bash
cd frontend
npm install
npm run build
cd ..
```

### 4. Start the Server
```bash
# Set PYTHONPATH and launch server
export PYTHONPATH=backend
python -m uvicorn codeworld.api.main:app --host 127.0.0.1 --port 8000
```

---

## Command Line Interface (CLI)

CodeWorld includes a comprehensive CLI:

```bash
# Check provider configuration and system health
codeworld doctor

# Index a repository
codeworld index ./path/to/repo

# Query the codebase with verified citations
codeworld ask ./path/to/repo "How is user session authentication validated?"

# Predict change impact of a modified file
codeworld impact ./path/to/repo --file auth/session.py

# Review a pull request patch with guardrails
codeworld review ./path/to/repo --diff changes.patch --sarif report.sarif

# Continuous watch mode (incrementally re-indexes on file change)
codeworld watch ./path/to/repo --interval 2.0

# Start Model Context Protocol (MCP) server for IDEs
codeworld mcp ./path/to/repo
```

---

## Model Context Protocol (MCP) Integration

CodeWorld can serve as a live knowledge graph provider for agentic editors like Cursor or Claude Code.

Add CodeWorld to your Claude Desktop or Cursor configuration:

```json
{
  "mcpServers": {
    "codeworld": {
      "command": "python",
      "args": [
        "-m",
        "codeworld",
        "mcp",
        "/absolute/path/to/your/repo"
      ],
      "env": {
        "PYTHONPATH": "/absolute/path/to/codeworld/backend"
      }
    }
  }
}
```

Available MCP tools include:
- `find_symbol`: Locate definitions by name or qualifier
- `get_callers`: Retrieve validated incoming callers from the graph
- `get_callees`: Retrieve outgoing function calls
- `get_impact`: Calculate blast radius and affected files/tests
- `get_architecture`: Inspect high-level module topology and dependencies
- `review_pr`: Run guardrail analysis against a unified diff

---

## Testing & Quality Assurance

CodeWorld includes an automated test suite comprising 65 tests covering all parsers, graph builders, resolvers, impact predictors, security scanners, and API endpoints.

```bash
# Run full backend test suite
pytest backend/tests -v

# Run code style and lint checks
ruff check backend
```

---

## Repository Structure

```
codeworld/
├── backend/
│   ├── codeworld/
│   │   ├── parser/         # Tree-sitter parsers (Python, JS/TS, Java, Go)
│   │   ├── graph/          # Knowledge graph builder and resolvers (Levels 0-4)
│   │   ├── ai/             # Intent classifier, BM25, embeddings, LLM client
│   │   ├── agent/          # Verified-citation Q&A engine and tools
│   │   ├── analysis/       # Impact analysis, PR guardrail, doc-drift, security
│   │   ├── evaluation/     # Benchmark harnesses and validation suites
│   │   ├── api/            # FastAPI REST endpoints
│   │   ├── cli.py          # Command line interface
│   │   └── mcp_server.py   # Model Context Protocol stdio server
│   ├── tests/              # Automated pytest test suites
│   └── requirements.txt    # Python dependencies
├── frontend/               # React + React Flow + Three.js web application
├── scripts/                # Setup and launch scripts for Linux, macOS, and Windows
├── .github/workflows/      # Continuous integration and PR review automation
└── README.md               # Project documentation
```

---

## License

CodeWorld is open-source software licensed under the [MIT License](LICENSE).
