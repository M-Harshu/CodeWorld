# Which LLM should you use on a 16 GB / i3 / no-GPU laptop?

**Short answer: use a free hosted API as the default and keep local models as an offline fallback.** Hardware reality check for a 10th-gen Dell Inspiron i3 (2 cores / 4 threads, no GPU, 16 GB RAM): a 3B model in 4-bit fits in RAM but generates slowly (expect a few tokens per second; 7B is slower still and makes the laptop unusable while it runs). Agent mode needs 3-6 model calls per question, so a hosted model is far more pleasant. CodeWorld itself (parsing, graph, retrieval, impact, guardrail) needs **no model at all**.

Provider facts below were gathered by web search in Nov 2026 - free tiers change monthly, so **always run `codeworld doctor --list-models` and read the provider's current limits page** before relying on a number.

## Your Google AI Plus subscription
Google AI Plus is a consumer plan (Gemini app, storage, creative tools). It is **not** the Gemini API: API usage is billed/quota'd separately from the consumer subscription, and Google's developer-program benefits are described for the higher AI Pro / Ultra plans, not AI Plus (see https://ai.google.dev/gemini-api/docs/google-ai-plans). What you *can* do for free: create an API key in **Google AI Studio** (https://aistudio.google.com/apikey, no card needed) and use the Gemini API **free tier** - rate-limited, Flash-class models, limits shown in AI Studio's rate-limit page. Check the terms for how free-tier prompts are handled before sending proprietary code.

```
# .env  (Gemini, OpenAI-compatible endpoint - already preset in CodeWorld)
CODEWORLD_LLM_PROVIDER=gemini
CODEWORLD_LLM_MODEL=<pick from:  codeworld doctor --list-models>
GEMINI_API_KEY=<AI Studio key>
CODEWORLD_LLM_RPM=8          # stay under the free per-minute cap; CodeWorld throttles + retries on 429
```
Why Gemini for this project: large context (you can pass bigger graph neighbourhoods), good code reasoning on Flash-class models, free tier, and the OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai`) means no special client. Embeddings via the API are optional - the offline `hash` embedder or a local MiniLM/CodeRankEmbed run fine on CPU.

## OpenRouter (DeepSeek, Qwen-Coder, Llama… via one key)
One API key, many models; models whose id ends in `:free` cost nothing. Published free-tier limits at the time of research: about 20 requests/minute and 50 requests/day, raised to ~1000/day if you have bought at least $10 of credits - fine for a demo, tight for experiments. The free list rotates, so list models at runtime.
```
CODEWORLD_LLM_PROVIDER=openrouter
CODEWORLD_LLM_MODEL=<an id ending in :free, e.g. a Qwen-Coder or DeepSeek variant from the list>
OPENROUTER_API_KEY=<key>
```

## Chain them (failover) - best use of free tiers
```
CODEWORLD_LLM_PROVIDER=gemini
CODEWORLD_LLM_MODEL=<gemini flash model id>
CODEWORLD_LLM_FALLBACKS=openrouter:<model id>:free,groq:<model id>,ollama:qwen2.5-coder:1.5b
GEMINI_API_KEY=...   OPENROUTER_API_KEY=...   GROQ_API_KEY=...
```
If a provider returns 429/5xx (quota exhausted, model rotated out) CodeWorld retries with back-off, then falls through to the next. Other presets built in: `groq`, `cerebras`, `mistral`, `github` (GitHub Models, uses your `GITHUB_TOKEN`), `nvidia`, plus `openai` for any OpenAI-compatible URL and `ollama` for local.

## Local (Ollama) on your laptop - only as fallback
`ollama pull qwen2.5-coder:1.5b` (or `:3b`) fits comfortably in 16 GB. Use it for offline demos; leave "agent tools" off with such small models (they often fail the JSON tool-call format - `codeworld doctor` tests this and tells you).

## Embeddings
`hash` (default) · `st` with `nomic-ai/CodeRankEmbed` (137M, CPU ok, ≈550 MB download, needs `pip install -r backend/requirements-optional.txt`) · `ollama` with `nomic-embed-text`. For the paper, run the QA experiments with both `hash` and CodeRankEmbed and report the difference.

## Privacy checklist
Free hosted tiers may log or train on prompts (read each provider's terms). CodeWorld sends only the retrieved snippets, never the whole repo. For private/employer code use the local model or the offline mode.
