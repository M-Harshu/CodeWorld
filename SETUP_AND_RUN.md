# Setup & Run - everything you need to do

## 0. What you need

| Need | Version | Check | Required? |
|---|---|---|---|
| Python | 3.10 or newer (3.12 tested) | `python3 --version` | **Yes** |
| git | any | `git --version` | **Yes** (cloning + history mining) |
| Node.js + npm | 18+ (22 tested) | `node --version` | **Required for git clones** (`frontend/dist` is git-ignored; a prebuilt dist is attached to each release). Skip only if you download the release zip. |
| RAM | 4 GB is enough for the default setup | | |
| GPU | **not needed** | | |
| Internet | only to clone GitHub repos / download optional models | | |

## 1. Install (once)

**macOS / Linux / WSL**
```bash
cd codeworld
./scripts/setup.sh
```
**Windows (PowerShell)**
```powershell
cd codeworld
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```
**Manual (any OS)**
```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt
pip install -e backend               # gives you the `codeworld` command; optional but handy
cp .env.example .env                 # Windows: copy .env.example .env
```

## 2. Start it

```bash
./scripts/run.sh                     # Windows: scripts\run.ps1
```
Open **http://localhost:8000**. Paste `https://github.com/pallets/click` (or any public repo) and press *Index repository*, or upload a `.zip`.
The first thing to try: **Overview → Code map → Ask → Impact → PR guardrail → Health → Code city**.

No repository handy? Index the bundled demo:
```bash
cd backend && python -m codeworld index tests/fixtures/shop_app     # then pick "shop_app" in the UI
```
(Indexes live in `~/.codeworld` by default, shared by the UI, CLI and MCP server, so the demo appears in the UI automatically. Override with `CODEWORLD_DATA_DIR`.)

### Run the UI in dev mode (hot reload)
```bash
# terminal 1
cd backend && CODEWORLD_ALLOW_LOCAL=1 python -m uvicorn codeworld.api.main:app --port 8000 --reload
# terminal 2
cd frontend && npm install && npm run dev          # http://localhost:5173
```
Rebuild the production UI after changes: `cd frontend && npm run build`.

## 3. Command-line usage (no UI needed)

If you did `pip install -e backend`, use `codeworld ...`; otherwise `cd backend && python -m codeworld ...`.

```bash
codeworld index  /path/to/repo                       # index (incremental on re-run)
codeworld repos
codeworld ask    /path/to/repo "How does login work?"
codeworld ask    /path/to/repo "Who calls verify_password?" --no-agent
codeworld impact /path/to/repo verify_password
codeworld onboard /path/to/repo --output ONBOARDING.md
codeworld health /path/to/repo
codeworld security /path/to/repo
codeworld review /path/to/repo --base origin/main --head HEAD          # AI-PR guardrail on a git range
codeworld review /path/to/repo --diff-file change.diff --json          # on a diff file (applied to a scratch copy)
codeworld serve --port 8000
```
`codeworld review` exits with status 1 when it finds a **high**-severity issue (`--fail-on medium|never` to change) - use it as a CI gate.

## 4. Turn on a real LLM (optional; everything works offline without one)

Without an LLM, answers are assembled from the graph (callers, impact, flows, tests, security) with verified citations. An LLM adds natural-language synthesis, multi-step tool use and patch proposals.

**On a 16 GB / i3 / no-GPU laptop use a hosted free tier, not Ollama** - see `docs/PROVIDERS.md` (includes the note that Google AI Plus is *not* the Gemini API, and how to get a free AI Studio key).

```
CODEWORLD_LLM_PROVIDER=gemini                 # or openrouter | groq | cerebras | mistral | github | nvidia | openai | ollama
CODEWORLD_LLM_MODEL=<model id>                # find ids with:  codeworld doctor --list-models
GEMINI_API_KEY=<key from https://aistudio.google.com/apikey>
CODEWORLD_LLM_RPM=8                           # throttle under the free per-minute limit
CODEWORLD_LLM_FALLBACKS=openrouter:<id>:free  # optional failover chain
```
Then check it end to end (reachability, latency, JSON tool-call compliance):
```bash
codeworld doctor --list-models
```
If a provider fails, CodeWorld retries with back-off, falls through your fallback chain, and finally shows the offline answer with a note.
Local option: `ollama pull qwen2.5-coder:1.5b` + `CODEWORLD_LLM_PROVIDER=ollama` (offline demos only on your hardware).

## 5. Better semantic search (optional)

The default `hash` embedder needs nothing. For real code embeddings on CPU:
```bash
pip install -r backend/requirements-optional.txt        # ~2 GB of dependencies
```
`.env`: `CODEWORLD_EMBED_BACKEND=st` and `CODEWORLD_EMBED_MODEL=nomic-ai/CodeRankEmbed` (downloads ≈ 550 MB on first use).
Or with Ollama: `ollama pull nomic-embed-text`, `CODEWORLD_EMBED_BACKEND=ollama`, `CODEWORLD_EMBED_MODEL=nomic-embed-text`.
Changing the embedding model re-embeds on the next `reindex` (vectors are cached per model). If the backend can't load, CodeWorld logs a warning and falls back to `hash`.

## 6. Use CodeWorld inside Claude Code / Cursor / Copilot (MCP)

```bash
# Claude Code
claude mcp add codeworld -- /abs/path/to/.venv/bin/python -m codeworld.mcp_server --repo /abs/path/to/your/repo
```
Cursor / other clients (`mcpServers` JSON):
```json
{ "mcpServers": { "codeworld": {
    "command": "/abs/path/to/.venv/bin/python",
    "args": ["-m", "codeworld.mcp_server", "--repo", "/abs/path/to/your/repo"],
    "env": { "PYTHONPATH": "/abs/path/to/codeworld/backend" } } } }
```
(`PYTHONPATH` is unnecessary after `pip install -e backend`.) The server indexes the repo on start (incremental) and exposes 16 tools: `search_code, find_symbol, get_symbol, get_file, find_callers, find_callees, trace_dependencies, find_tests, impact, explain_flow, repo_overview, list_endpoints, hotspots, health_summary, security_summary, review_diff`. See `docs/MCP.md`.

## 6b. GitHub, VS Code and agent features

```bash
export GITHUB_TOKEN=<fine-grained token: read access; add Pull requests/Issues write to post comments>
codeworld github review-pr  owner/repo 123 [--post]     # reviews any PR from your laptop (base snapshot + the PR diff)
codeworld github issue      owner/repo 45  [--post]     # "which files/functions does this issue probably concern?"
codeworld fix <repo> "make the token length configurable" --out change.diff   # LLM-drafted patch, guardrail-reviewed, never applied
codeworld trace <repo> --run "pytest -q" --yes          # OPTIONAL: run tests, merge observed calls into the graph (executes code!)
```
VS Code (and forks that accept VSIX: Cursor, VSCodium, Antigravity): install `vscode-extension/codeworld-1.0.0.vsix` (*Extensions → … → Install from VSIX*), run **CodeWorld: Select repository**. You get CodeLens (callers/tests/risk), Ask, Impact-at-cursor, Review-my-changes (findings in the Problems panel) and Propose-patch.
Set `CODEWORLD_API_TOKEN` on the server (and `codeworld.apiToken` in VS Code / the token prompt in the UI) if anyone else can reach the port.

## 7. Guard your pull requests (CI)

Copy `.github/workflows/codeworld-review.yml` into *your* repository, replace `<YOUR-ORG>/codeworld` with where you host this project, commit. It comments the report on every PR and fails the check on high-severity findings. (Template - test it on a throwaway repo first.)

To define your own architecture rules put a `.codeworld.yml` in the analysed repo (see `.codeworld.yml.example`).

## 8. Research experiments (for the paper)

One command reproduces the pilot (clones 5 public repos, installs their test deps into the *active* Python environment, traces their tests, writes `results/.../report.md`, `summary.csv`, PNG figures):
```bash
python -m codeworld.evaluation.suite --repos click --out results/quick --work suite_work     # ~5 min
python -m codeworld.evaluation.suite --repos all   --out results/full  --work suite_work     # ~40 min
```
Best run it inside a throw-away venv or container (it pip-installs those projects and executes their tests). Individual experiments:
```bash
codeworld index /path/to/repo
codeworld eval qa     /path/to/repo --dataset my_questions.json --modes bm25,vector,hybrid,graph,agentic --k 5 --out qa.json
codeworld eval impact /path/to/repo --commits 400 --out impact.json         # needs a clone WITH history
python -m codeworld.evaluation.dynamic_trace --root /path/to/repo --run "pytest -q tests" --out truth.json   # runs the repo's code! use a container
codeworld eval callgraph /path/to/repo --truth truth.json
codeworld eval resolver-ablation /path/to/repo --truth truth.json --dataset my_questions.json
```
Dataset format, protocol, threats to validity and a paper outline: `docs/RESEARCH.md` and `docs/research/`.

## 9. Tests

```bash
cd backend && python -m pytest           # 53 tests (34 original + 7 fixpack + 12 config/features/analysis tests)
cd vscode-extension && node test/smoke.js
codeworld doctor                         # environment + provider self-test
```

## 10. Docker - you probably do not need it

Docker was **not** testable in the development sandbox (the Dockerfile/compose file are untested; GitHub's `ci.yml` builds and health-checks the image for you once you push the project). On a Windows laptop Docker Desktop needs virtualisation/WSL2 and takes a few GB of RAM - skip it unless you must deploy. The `scripts/run.ps1` path is simpler and uses less memory. Check Docker's current licence terms before using Docker Desktop at a company.

```bash
cp .env.example .env
docker compose up --build                 # http://localhost:8000
docker compose --profile llm up -d        # optional Ollama container
```

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: codeworld` | Run from `backend/`, or `pip install -e backend`, or set `PYTHONPATH=backend` |
| UI shows "Cannot GET /" or an API-only message | `frontend/dist` is missing: `cd frontend && npm install && npm run build` |
| `git clone failed` | Only `https://` URLs on github.com / gitlab.com / bitbucket.org / codeberg.org are allowed (edit `CODEWORLD_GIT_HOSTS`). Private repos: clone yourself and upload a zip or use the CLI on the folder |
| "no history" in Impact / Health | You indexed a ZIP or `--depth 1`. Use a git clone (default depth 1000) |
| Local-path indexing refused by the API | Set `CODEWORLD_ALLOW_LOCAL=1` (CLI never needs it) |
| Slow first embedding run with `st` | Model download + CPU inference; later runs reuse cached vectors |
| LLM answers say "LLM call failed" | Check Ollama is running (`ollama list`) / the API key / base URL; the offline answer is shown meanwhile |
| Windows: `sqlite3 ... fts5` error | Use the python.org Python 3.10+ build (FTS5 is included) |
| Model picker in Ask shows wrong model IDs | Rotate free-tier model IDs in `.env`: `CODEWORLD_MODEL_GEMINI`, `CODEWORLD_MODEL_OPENROUTER`, `CODEWORLD_MODEL_OLLAMA` |
| `psutil` not found (peak memory shows 0) | `pip install psutil>=5.9` (now in `requirements.txt`; reinstall if your venv is older) |

## 12. What NOT to do

- **Do not expose the API to the internet without `CODEWORLD_API_TOKEN` (and a reverse proxy with TLS).** The token is a shared secret, not user management. Keep `CODEWORLD_ALLOW_LOCAL=0` on any shared host.
- **Do not run `dynamic_trace` on code you don't trust** - it executes the repository's tests. CodeWorld itself never executes analysed code, that script is the one exception.
- Do not treat the security view as an audit (it is call-graph triage); do not treat "dead code" on a library as deletable without checking its public API.
- Do not report numbers in the paper that you did not produce with the harness on your own datasets.
- Do not commit `.env` (it is in `.gitignore`).
- Do not delete the data folder (`~/.codeworld`) while the server runs; use the UI's *Delete* button or `DELETE /api/repos/{id}`.
