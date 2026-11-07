# CodeWorld for VS Code

A thin client for a running CodeWorld server (`./scripts/run.sh`, default http://localhost:8000). Works in VS Code and in forks that accept VSIX files (Cursor, VSCodium, Antigravity - install via *Extensions → … → Install from VSIX*).

* **CodeLens** above every function: callers · tested ✓/✗ · endpoint · risky side-effects
* **Ask the codebase** (graph-RAG answer with verified, clickable citations)
* **Impact of symbol at cursor** (dependents, endpoints, tests to run, co-changed files)
* **Review my uncommitted changes**: runs the PR guardrail and shows findings in the Problems panel
* **Propose a patch** (needs an LLM on the server): returns a reviewed diff in an editor tab; nothing is applied
* **Open code map** in the browser, focused on the current file

Setup: index your repository (web UI or `codeworld index .`), install the VSIX, run **CodeWorld: Select repository**. If the server uses `CODEWORLD_API_TOKEN`, set `codeworld.apiToken`.
Build the VSIX: `npx @vscode/vsce package --no-dependencies --allow-missing-repository`.
Not tested inside a real VS Code window (only against a stub of the API): report issues.
