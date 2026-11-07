# CodeWorld RUNBOOK - every command, from zero (Windows PowerShell; bash equivalents at the end)

Replace `C:\path\to\repo` with your clone (the folder that contains `backend\`, `frontend\`, `scripts\`).

## A. One-time installs
```powershell
winget install -e --id Python.Python.3.12        # or python.org installer, tick "Add to PATH"
winget install -e --id Git.Git
winget install -e --id OpenJS.NodeJS.LTS          # needed to build the UI / VS Code extension
winget install -e --id Ollama.Ollama              # only if you want a local model
# close and reopen PowerShell, then verify:
python --version ; git --version ; node --version
```

## B. Get the code and fix the repo layout (do once)
Your GitHub repo has everything inside a `codeworld\` subfolder, so GitHub ignores `.github\workflows`. Move it up to the root:
```powershell
cd C:\path\to\your\clone-of-codeworld
git mv codeworld\.github .github
git mv codeworld\.gitignore .gitignore
git mv codeworld\.env.example .env.example
git mv codeworld\.codeworld.yml.example .codeworld.yml.example
Get-ChildItem codeworld -Force | Where-Object Name -notin '.github','.gitignore' | ForEach-Object { git mv $_.FullName . }
git rm -r --cached --ignore-unmatch server.log suite_work results/quick
git commit -m "Move project to repo root so GitHub Actions run; stop tracking logs/work dirs"
git push
```
Then append to `.gitignore` (see chat for the exact lines), commit, push, and open the **Actions** tab - `CI` now runs the tests on Windows/macOS/Linux and builds Docker.

## C. Install and configure
```powershell
cd C:\path\to\repo
python -m venv .venv
.\.venv\Scripts\Activate.ps1                       # if blocked: Set-ExecutionPolicy -Scope Process Bypass
python -m pip install -U pip
pip install -r backend\requirements-dev.txt
pip install -e backend                              # gives the `codeworld` command
copy .env.example .env                              # then edit .env (section D)
cd frontend ; npm install ; npm run build ; cd ..   # builds frontend\dist (served by the API)
```

## D. `.env` (edit in Notepad; no quotes, no spaces around `=`)
```
CODEWORLD_LLM_PROVIDER=gemini
GEMINI_API_KEY=your-AI-Studio-key
CODEWORLD_MODEL_GEMINI=<id from: codeworld doctor --list-models>
CODEWORLD_LLM_MODEL=<same id>
OPENROUTER_API_KEY=your-openrouter-key
CODEWORLD_MODEL_OPENROUTER=<an id ending in :free>
CODEWORLD_MODEL_OLLAMA=qwen3.5:4b
CODEWORLD_LLM_RPM=8
CODEWORLD_LLM_FALLBACKS=openrouter:<id>:free
CODEWORLD_EMBED_BACKEND=hash
CODEWORLD_ALLOW_LOCAL=1
```

## E. Ollama (local, optional)
```powershell
ollama --version
ollama pull qwen3.5:4b            # or qwen2.5-coder:1.5b if RAM is tight
ollama list
ollama run qwen3.5:4b "say hi"    # Ctrl+D to exit
# in .env:  CODEWORLD_LLM_PROVIDER=ollama   CODEWORLD_LLM_MODEL=qwen3.5:4b   CODEWORLD_LLM_BASE_URL=http://localhost:11434
```

## F. Check everything
```powershell
codeworld doctor --list-models     # every line should be [ ok ] (warn for docker is fine)
cd backend ; python -m pytest ; cd ..                  # expect: all passed
cd vscode-extension ; node test\smoke.js ; cd ..       # expect: extension smoke test OK
```

## G. Run the app
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1      # open http://localhost:8000
# dev mode with hot reload (two windows):
#   window 1:  cd backend ; $env:CODEWORLD_ALLOW_LOCAL="1" ; python -m uvicorn codeworld.api.main:app --port 8000 --reload
#   window 2:  cd frontend ; npm run dev        -> http://localhost:5173
```
Index from the command line (appears in the UI automatically):
```powershell
codeworld index C:\path\to\any\project
codeworld index backend\tests\fixtures\shop_app           # bundled demo
codeworld repos
codeworld ask backend\tests\fixtures\shop_app "who calls hash_password?"
codeworld impact backend\tests\fixtures\shop_app verify_password
codeworld health backend\tests\fixtures\shop_app
codeworld security backend\tests\fixtures\shop_app
codeworld onboard backend\tests\fixtures\shop_app --output ONBOARDING.md
codeworld review C:\path\to\project --base origin/main --head HEAD
codeworld review C:\path\to\project --diff-file change.diff
codeworld fix C:\path\to\project "make the token length configurable" --out change.diff   # needs an LLM
```

## H. GitHub from your laptop
```powershell
$env:GITHUB_TOKEN = "github_pat_..."          # fine-grained token; add PR/Issue write to post comments
codeworld github review-pr owner/repo 123
codeworld github review-pr owner/repo 123 --post
codeworld github issue owner/repo 45
```
Normal git workflow for your own changes:
```powershell
git status ; git add -A ; git commit -m "message" ; git push
git pull --rebase                              # get CI/other changes
git checkout -b feature/x ; git push -u origin feature/x      # then open a Pull Request on GitHub
```

## I. VS Code extension
```powershell
cd vscode-extension
npx --yes @vscode/vsce package --no-dependencies --allow-missing-repository --skip-license
code --install-extension codeworld-1.0.0.vsix        # Cursor: cursor --install-extension ...
```
In VS Code: Ctrl+Shift+P → "CodeWorld: Select repository".

## J. MCP for Claude Code / Cursor
```powershell
claude mcp add codeworld -- C:\path\to\repo\.venv\Scripts\python.exe -m codeworld.mcp_server --repo C:\path\to\your\project
```

## K. Benchmark and evaluation experiments
### K1. Pilot suite (retrieval, call-graph fidelity, impact, guardrail) - no LLM needed
Use a throw-away venv: the suite pip-installs the test dependencies of the repos it analyses and executes their tests.
```powershell
python -m venv .venv-exp ; .\.venv-exp\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt ; pip install -e backend ; pip install matplotlib psutil
python -m codeworld.evaluation.suite --repos click --out results\quick --work suite_work          # ~5 min
python -m codeworld.evaluation.suite --repos all   --out results\full  --work suite_work          # ~40+ min
python -m codeworld.evaluation.suite --aggregate-only --out results\full                          # rebuild report/figures
# one step only: --steps scale,doc2code,callgraph,callers,impact,guardrail,commits
```
Outputs: `results\full\report.md`, `summary.csv`, `fig_*.png`, one JSON per repo.

### K2. Real embeddings (CodeRankEmbed) - repeat K1 and compare
```powershell
pip install -r backend\requirements-optional.txt
$env:CODEWORLD_EMBED_BACKEND="st" ; $env:CODEWORLD_EMBED_MODEL="nomic-ai/CodeRankEmbed"
python -m codeworld.evaluation.suite --repos click,flask --steps scale,doc2code,callers --out results\coderank --work suite_work
```
### K3. LLM / agent experiments (RQ1: does the graph help small models more?)
```powershell
python scripts\make_dataset.py click 60 results\click_qa.json        # script text is in the chat answer
codeworld index suite_work\click
# one run per model - change the env vars between runs:
$env:CODEWORLD_LLM_PROVIDER="gemini" ; $env:CODEWORLD_LLM_MODEL="<id>" ; $env:CODEWORLD_LLM_RPM="8"
codeworld eval qa suite_work\click --dataset results\click_qa.json --modes bm25,hybrid,auto,agentic --k 5 --out results\qa_gemini.json
$env:CODEWORLD_LLM_PROVIDER="ollama" ; $env:CODEWORLD_LLM_MODEL="qwen3.5:4b"
codeworld eval qa suite_work\click --dataset results\click_qa.json --modes bm25,hybrid,auto,agentic --k 5 --out results\qa_qwen4b.json
$env:CODEWORLD_LLM_PROVIDER="none"
codeworld eval qa suite_work\click --dataset results\click_qa.json --modes bm25,hybrid,auto,agentic --k 5 --out results\qa_offline.json
```
Compare `citation_precision`, `sym_R@5`, `latency_ms`, `tokens` across the three JSON files. Free tiers cap requests per day: 60 questions x ~4 agent calls per model; lower the dataset size if you hit 429s.
### K4. Resolver ablation / impact / guardrail individually
```powershell
python -m codeworld.evaluation.dynamic_trace --root suite_work\click --run "pytest -q tests" --out results\click_truth.json
codeworld eval resolver-ablation suite_work\click --truth results\click_truth.json --out results\ablation.json
codeworld eval impact suite_work\click --commits 400 --out results\impact.json
codeworld eval guardrail suite_work\click --out results\guardrail.json
```
### K5. User study analysis
```powershell
python docs\research\analyze_study.py results\study.csv results\sus.csv
```

## L. Troubleshooting one-liners
```powershell
.\.venv\Scripts\Activate.ps1          # fixes "codeworld not recognised"
pip install -r backend\requirements-dev.txt     # fixes "No module named ..."
Get-Content .env                      # check for stray quotes/spaces
codeworld doctor                      # tells you what is missing
netstat -ano | findstr :8000          # port already used? then:  taskkill /PID <pid> /F
```

## Bash / macOS / Linux equivalents
`source .venv/bin/activate` instead of `Activate.ps1`; `export VAR=value` instead of `$env:VAR="value"`; `cp` instead of `copy`; `./scripts/setup.sh` and `./scripts/run.sh` instead of the `.ps1` scripts.
