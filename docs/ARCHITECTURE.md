# Architecture

## Pipeline

```
scan_files ─► parse (tree-sitter) ─► parsed table (JSON per file, keyed by sha)
                                         │
                     GraphBuilder.resolve (level 0-3) ─► nodes + edges tables
                                         │
        chunk (AST-aware) ─► FTS5 (BM25)   +   embeddings (cached by text hash + model)
                                         │
                      git mining ─► churn, authorship, co-change pairs
```
Everything is stored per repository in one SQLite file (`~/.codeworld/repos/<id>/index.db`); a registry database lists repositories and jobs. Re-indexing is incremental: unchanged files are not re-parsed, unchanged chunks are not re-embedded; the graph is rebuilt from the stored parse results (cheap).

## Graph model

Nodes: `repo, module (directory), file, class, function, method, test, api, effect, external`.
Edges: `CONTAINS, IMPORTS, CALLS, INHERITS, IMPLEMENTS, EXPOSES (handler→endpoint), TESTS, ACCESSES (symbol→effect such as db/network/fs/process)`.
Every `CALLS` edge stores `conf` (0-1) and `res` (the rule that produced it: `scope`, `import`, `self`, `typed`, `super`, `ctor`, `route`, `jedi`, `name`).

### Call resolution (the part that decides quality)
| Level | Adds | Typical use |
|---|---|---|
| 0 | link every symbol sharing the called name | naive baseline for the ablation |
| 1 | local scope, module scope, import aliases (incl. relative imports and re-exports), constructors | |
| **2** | `self.x()`, `self.attr.method()` with attribute types from annotations / `__init__` injection / `x = Foo()`, annotated parameters, `super()`, FastAPI `Depends(f)`, test→endpoint links by URL string, unique-name fallback with reduced confidence | **default** |
| 3 | asks Jedi to resolve what level 2 could not | optional |

Known blind spots: duck-typed calls into test doubles, `getattr`/reflection, decorators that rewrite callables, framework route prefixes, cross-language calls.

## Retrieval

`bm25` (FTS5 with identifier splitting and light stemming) · `vector` (cosine over cached embeddings) · `hybrid` (reciprocal-rank fusion) · `graph` (hybrid, then expand from the top seeds and explicitly named symbols over CALLS/INHERITS with distance decay; callers are boosted for "who calls…", callees for "how does… work"). The context given to an LLM includes the relationships among the chosen snippets.

## Answering

1. Classify intent (rules) and detect symbol mentions.
2. With an LLM + agent enabled: a JSON tool-call loop (max 5 tools) over the shared `ToolRegistry`.
3. Without one: an offline planner picks tools by intent and composes an extractive answer from tool results.
4. Every `[path:start-end]` citation in the final text is checked against the ranges actually surfaced by retrieval/tools → `citation_precision`.

## Analyses
`impact` reverse BFS on CALLS/INHERITS + importers + co-change + tests + risk score · `health` cycles (SCC), dead code, hotspots (churn × complexity), duplicates (normalised token shingles, Jaccard), layering (directory keywords or `.codeworld.yml`), graph-coverage, bus factor, Louvain clusters · `security` sinks + reachability from endpoints + secret regexes · `diffreview` (see README) · `onboarding` · `city` (treemap layout for the 3D view).

## PR guardrail details
The diff is applied to a scratch copy of the indexed snapshot (`git apply`, no execution), re-indexed incrementally, and checked: hallucinated imports (package not in repo/stdlib/installed/declared deps; `from m import name` where `m` defines no `name`; `module.func()` with no such member), duplicate logic, layering/cycles, untested changed symbols, sinks reachable from endpoints, removed-but-still-called symbols, complexity, and blast radius. In CI the checked-out tree already is the PR head, so `review_diff` is used directly.

## Security posture
URL allow-list (https + host list + path regex, no credentials), `git clone` with hooks disabled and `protocol.file.allow=never`, size/file caps, zip-slip/symlink/zip-bomb-safe extraction, path-traversal-safe file reads, repository code is never imported or executed (the only exception is the opt-in `dynamic_trace` research script). The API has **no authentication** by design (local tool).

## Extending
Add a language: write `parser/<lang>_parser.py` returning `ParseResult`, register it in `parser/__init__.py` and `ingest.CODE_EXT`; the resolver needs import-resolution rules for that language. Add a tool: write `t_<name>` in `agent/tools.py` and a spec line; it appears in REST, the agent and MCP automatically.
