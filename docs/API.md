# REST API (all under `/api`)

Interactive docs: `http://localhost:8000/docs` (FastAPI Swagger UI).

| Method & path | Purpose |
|---|---|
| `GET /health` | provider / embedding / resolver status |
| `GET /repos` · `DELETE /repos/{id}` | list / delete |
| `POST /repos` `{url, branch?}` | clone + index (returns `repo_id`, `job_id`) |
| `POST /repos/upload` (multipart `file`=.zip) | upload + index |
| `POST /repos/local` `{path}` | index a server-side folder (needs `CODEWORLD_ALLOW_LOCAL=1`) |
| `GET /jobs/{id}` | progress `{stage, pct, status, error}` |
| `POST /repos/{id}/reindex?force=` | incremental re-index |
| `GET /repos/{id}/stats \| tree \| file?path= \| node?id= \| ancestors?id=` | repository browsing |
| `GET /repos/{id}/graph?expanded=a\|b&types=CALLS,IMPORTS&tests=&min_conf=&focus=&highlight=` | semantic-zoom graph view |
| `GET /repos/{id}/search?q=&mode=bm25\|vector\|hybrid\|graph&k=` | retrieval only |
| `POST /repos/{id}/ask` `{question, mode, agent, history}` | answer + citations + trace |
| `GET /repos/{id}/impact?target=&depth=&min_conf=` | impact analysis |
| `POST /repos/{id}/review` `{diff}` or `{base, head}` (`applied` = index already contains the change) | PR guardrail |
| `GET /repos/{id}/health \| security \| onboarding \| onboarding.md \| city \| cochange?path=` | analyses |
| `GET /repos/{id}/tools` · `POST /repos/{id}/tools/{name}` `{args}` | raw tool access (same as MCP) |
