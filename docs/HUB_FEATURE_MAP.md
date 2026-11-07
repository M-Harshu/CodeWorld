# One hub for code: what the big tools do, and what CodeWorld covers on your hardware

Competitor facts come from 2026 web comparisons (Antigravity vs Cursor etc.); vendors change features quickly - verify before quoting in the paper.

| Capability (as seen in Antigravity / Cursor / VS Code + Copilot / GitHub) | CodeWorld | Status |
|---|---|---|
| Codebase-aware chat with @-style context | Ask page, CLI, VS Code *Ask*, MCP tools | **built** (graph-RAG + verified citations; stronger with an LLM) |
| Codebase indexing | Tree-sitter graph + BM25 + vectors, incremental | **built** |
| Inline code lens / navigation aids | CodeLens: callers · tested · endpoint · risky effects; code map; 3D city | **built** (extension tested against a stub only) |
| Agent that edits code | `codeworld fix` / API `propose` / VS Code *Propose patch*: SEARCH-REPLACE edits → unified diff → **graph guardrail** → you apply | **built**, needs an LLM; never auto-applies |
| PR review bot (Cursor Bugbot / Copilot review) | `codeworld review`, GitHub Action template, `codeworld github review-pr` | **built**; static graph checks, not an LLM reviewer |
| Issue triage / localisation | `codeworld github issue`, Action template | **built** |
| Test selection / "what will this break" | Impact + co-change + test list | **built** (research-validated, see results) |
| MCP for coding agents | stdio server, 16 tools | **built** (raw-JSON-RPC verified only) |
| Onboarding docs / architecture diagrams | Onboarding guide generator, Mermaid flows | **built** |
| Security triage | Attack-surface map, secret scan | **built** (heuristic) |
| Agent Manager: many agents in parallel (Antigravity) | - | not built; needs paid-tier throughput and a scheduler |
| Browser/computer-use agent + screenshots (Antigravity) | - | out of scope on this hardware |
| Low-latency inline autocomplete (Cursor Tab, Copilot) | - | out of scope: needs a fast small model + editor integration; use Copilot/Continue alongside CodeWorld |
| Cloud background agents / remote sandboxes | - | out of scope; CI templates cover the async part |
| Run tests in a sandbox for the agent | `codeworld trace` runs tests (opt-in, **executes code**) | partial |
| Java/Go/Rust/C++ support | JS/TS/Python only | roadmap: add a parser per language |

**How CodeWorld complements those tools instead of competing:** it is the *structure and safety layer* - give any of them the graph through MCP, and let the guardrail check what they write.
