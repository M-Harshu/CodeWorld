# MCP server

`python -m codeworld.mcp_server --repo <repo-id | path>` speaks MCP over stdio (JSON-RPC 2.0, protocol `2024-11-05`, echoes the client's version) with no third-party dependency. Methods: `initialize`, `tools/list`, `tools/call`, `ping`.

**Why**: coding agents spend most tokens grepping and reading files to rebuild context. These tools answer structural questions directly ("who calls X", "what breaks if I change Y", "which tests cover Z") from the graph. Published work on code-graph MCP servers reports large token reductions at a modest answer-quality cost; measure it for CodeWorld yourself (RQ in `docs/RESEARCH.md`).

Tools: `search_code, find_symbol, get_symbol, get_file, find_callers, find_callees, trace_dependencies, find_tests, impact, explain_flow, repo_overview, list_endpoints, hotspots, health_summary, security_summary, review_diff`.

Verified: handshake, `tools/list`, `tools/call find_callers` with a raw JSON-RPC client (see `backend/tests/test_review_ingest_api.py::test_mcp_stdio`). Not verified with Claude Code / Cursor / Copilot / Cline themselves - client configuration snippets are in `SETUP_AND_RUN.md` §6.

Tip: put this in your agent's instructions: *"Before reading files to answer a structural question, call the codeworld tools; run `impact` and `find_tests` before editing shared code; run `review_diff` on your own changes."*
