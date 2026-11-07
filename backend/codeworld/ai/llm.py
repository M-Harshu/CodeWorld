"""LLM providers. Presets for the free/cheap hosted APIs, Ollama for local, automatic retry/backoff, request throttling and failover.

Provider names:  none | ollama | openai (any OpenAI-compatible URL) | gemini | openrouter | groq | cerebras | mistral | github | nvidia
Fallback chain:  CODEWORLD_LLM_FALLBACKS="openrouter:qwen/qwen3-coder:free,groq:llama-3.3-70b-versatile"   (provider:model, comma separated)
"""
from __future__ import annotations
import logging
import os
import threading
import time

import httpx

log = logging.getLogger("codeworld.llm")

PRESETS = {   # base_url, env var holding the key
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "github": ("https://models.github.ai/inference", "GITHUB_TOKEN"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
}


class LLMError(RuntimeError):
    pass


class LLM:
    name = "none"
    available = False

    def __init__(self):
        self.calls = 0
        self.failures = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.seconds = 0.0

    def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 900) -> str:
        raise LLMError("no LLM configured")

    def usage(self) -> dict:
        return {"provider": self.name, "calls": self.calls, "failures": self.failures, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "seconds": round(self.seconds, 2)}


class NullLLM(LLM):
    pass


class _Throttle:
    """Keeps requests under a per-minute budget (free tiers are request-limited, not token-limited)."""
    def __init__(self, rpm: float):
        self.interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
        self.last = 0.0
        self.lock = threading.Lock()

    def wait(self):
        if not self.interval:
            return
        with self.lock:
            delta = time.time() - self.last
            if delta < self.interval:
                time.sleep(self.interval - delta)
            self.last = time.time()


class HTTPLLM(LLM):
    available = True

    def __init__(self, label: str, model: str, rpm: float = 0, retries: int = 4):
        super().__init__()
        self.model, self.name, self.retries = model, f"{label}:{model}", retries
        self.throttle = _Throttle(rpm)

    def _post(self, url: str, payload: dict, headers: dict, timeout: float) -> dict:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self.throttle.wait()
            t0 = time.time()
            try:
                r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
                self.seconds += time.time() - t0
                if r.status_code == 429 or r.status_code >= 500:
                    ra = r.headers.get("retry-after")
                    wait = min(60.0, float(ra)) if ra and ra.replace(".", "", 1).isdigit() else min(60.0, 2.0 ** attempt * 2)
                    last = LLMError(f"HTTP {r.status_code}: {r.text[:160]}")
                    log.warning("%s -> %s, retrying in %.0fs", self.name, r.status_code, wait)
                    time.sleep(wait)
                    continue
                if r.status_code in (401, 403):
                    raise LLMError(f"HTTP {r.status_code}: authentication failed - check the API key ({r.text[:120]})")
                r.raise_for_status()
                return r.json()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                self.seconds += time.time() - t0
                last = e
                time.sleep(min(30.0, 2.0 ** attempt))
        self.failures += 1
        raise LLMError(f"{self.name} failed after {self.retries + 1} attempts: {last}")


class OllamaLLM(HTTPLLM):
    def __init__(self, model: str, base: str, rpm: float = 0):
        super().__init__("ollama", model, rpm)
        self.label = "ollama"
        self.base = base.rstrip("/")

    def chat(self, messages, temperature=0.1, max_tokens=900):
        self.calls += 1
        clean_msgs = []
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                clean_msgs.append({**m, "content": " ".join(text_parts)})
            else:
                clean_msgs.append(m)
        payload = {
            "model": self.model,
            "messages": clean_msgs,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 4096}
        }
        d = self._post(f"{self.base}/api/chat", payload, {}, 600)
        self.tokens_in += d.get("prompt_eval_count", 0)
        self.tokens_out += d.get("eval_count", 0)
        msg = d.get("message", {})
        content = msg.get("content", "")
        if not content and msg.get("thinking"):
            content = msg.get("thinking")
        return content or ""


class OpenAICompatLLM(HTTPLLM):
    def __init__(self, label: str, model: str, base: str, key: str, rpm: float = 0):
        super().__init__(label, model, rpm)
        self.label = label
        self.base, self.key = base.rstrip("/"), key
        self.last_reasoning_details = None

    def chat(self, messages, temperature=0.1, max_tokens=900):
        self.calls += 1
        h = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        is_vision = (self.label == "gemini")
        clean_msgs = []
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list) and not is_vision:
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                clean_msgs.append({**m, "content": " ".join(text_parts)})
            else:
                clean_msgs.append(m)

        payload = {"model": self.model, "messages": clean_msgs, "temperature": temperature, "max_tokens": max(max_tokens, 4000)}
        if self.label == "openrouter" or "openrouter.ai" in self.base:
            payload["reasoning"] = {"enabled": True}
        d = self._post(f"{self.base}/chat/completions", payload, h, 180)
        u = d.get("usage") or {}
        self.tokens_in += u.get("prompt_tokens", 0)
        self.tokens_out += u.get("completion_tokens", 0)
        try:
            choice = d["choices"][0]
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            if "reasoning_details" in msg:
                self.last_reasoning_details = msg["reasoning_details"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"unexpected response from {self.name}: {str(d)[:200]}")
        return content or ""

    def list_models(self) -> list[str]:
        h = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        r = httpx.get(f"{self.base}/models", headers=h, timeout=30)
        r.raise_for_status()
        return sorted(m.get("id", "").removeprefix("models/") for m in r.json().get("data", []))


class ChainLLM(LLM):
    """Try providers in order; a failure on one falls through to the next (quota exhausted, model rotated out, ...)."""
    available = True

    def __init__(self, parts: list[LLM]):
        super().__init__()
        self.parts, self.name = parts, " -> ".join(p.name for p in parts)

    @property
    def retries(self):
        return self.parts[0].retries if self.parts else 0

    @retries.setter
    def retries(self, val):
        for p in self.parts:
            p.retries = val

    def list_models(self) -> list[str]:
        for p in self.parts:
            if hasattr(p, "list_models"):
                return p.list_models()
        return []

    def chat(self, messages, temperature=0.1, max_tokens=900):
        self.calls += 1
        errs = []
        for p in self.parts:
            try:
                res = p.chat(messages, temperature, max_tokens)
                if hasattr(p, "last_reasoning_details"):
                    self.last_reasoning_details = p.last_reasoning_details
                return res
            except Exception as e:
                errs.append(f"{p.name}: {e}")
                log.warning("LLM %s failed, trying next: %s", p.name, e)
        self.failures += 1
        raise LLMError("all providers failed: " + " | ".join(errs))

    def usage(self) -> dict:
        return {"provider": self.name, "calls": self.calls, "failures": self.failures,
                "tokens_in": sum(p.tokens_in for p in self.parts), "tokens_out": sum(p.tokens_out for p in self.parts),
                "seconds": round(sum(p.seconds for p in self.parts), 2), "parts": [p.usage() for p in self.parts]}


def get_available_models(settings) -> list[dict]:
    """Return available models driven from environment variables.

    Env vars (set in .env or shell) control which model ID each provider uses,
    so there's no need to edit code when model IDs rotate on free tiers:
      CODEWORLD_MODEL_GEMINI     (default: gemini-3.5-flash-lite)
      CODEWORLD_MODEL_OPENROUTER (default: nvidia/nemotron-3-ultra-550b-a55b:free)
      CODEWORLD_MODEL_OLLAMA     (default: qwen3.5:4b)
    """
    gemini_key = os.environ.get("GEMINI_API_KEY", "") or settings.llm_api_key
    openrouter_key = (os.environ.get("OPENROUTER_API_KEY", "") or
                      os.environ.get("CODEWORLD_LLM_API_KEY", "") or
                      settings.llm_api_key)

    # Model IDs from env (fall back to sensible defaults)
    gemini_model = os.environ.get("CODEWORLD_MODEL_GEMINI", "gemini-3.5-flash-lite")
    openrouter_model = os.environ.get("CODEWORLD_MODEL_OPENROUTER", "nvidia/nemotron-3-ultra-550b-a55b:free")
    ollama_model = (os.environ.get("CODEWORLD_MODEL_OLLAMA") or
                    os.environ.get("CODEWORLD_OLLAMA_MODEL") or "qwen3.5:4b")

    return [
        {
            "id": "gemini",
            "provider": "gemini",
            "model": gemini_model,
            "name": f"Google Gemini ({gemini_model})",
            "badge": "Gemini · Fast · Vision",
            "vision": True,
            "configured": bool(gemini_key),
        },
        {
            "id": "openrouter",
            "provider": "openrouter",
            "model": openrouter_model,
            "name": f"OpenRouter ({openrouter_model.split('/')[-1]})",
            "badge": "OpenRouter · Reasoning",
            "vision": False,
            "configured": bool(openrouter_key),
        },
        {
            "id": "ollama",
            "provider": "ollama",
            "model": ollama_model,
            "name": f"Ollama Local ({ollama_model})",
            "badge": "Local · Offline CPU",
            "vision": False,
            "configured": True,
        },
    ]


def _build(provider: str, model: str | None, settings) -> LLM:
    rpm = float(os.environ.get("CODEWORLD_LLM_RPM", "0") or 0)
    if provider == "ollama":
        m = model or os.environ.get("CODEWORLD_OLLAMA_MODEL") or "qwen3.5:4b"
        return OllamaLLM(m, settings.llm_base_url, rpm)
    if provider == "gemini":
        m = model or "gemini-3.5-flash-lite"
        key = os.environ.get("GEMINI_API_KEY", "") or settings.llm_api_key
        if not key:
            raise LLMError("gemini: set GEMINI_API_KEY")
        base = PRESETS["gemini"][0]
        return OpenAICompatLLM("gemini", m, base, key, rpm)
    if provider == "openrouter":
        m = model or "nvidia/nemotron-3-ultra-550b-a55b:free"
        key = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("CODEWORLD_LLM_API_KEY", "") or settings.llm_api_key
        if not key:
            raise LLMError("openrouter: set OPENROUTER_API_KEY")
        base = "https://openrouter.ai/api/v1"
        return OpenAICompatLLM("openrouter", m, base, key, rpm)
    if provider in PRESETS:
        base, env = PRESETS[provider]
        key = os.environ.get(env, "") or settings.llm_api_key
        if not key:
            raise LLMError(f"{provider}: set {env} (or CODEWORLD_LLM_API_KEY)")
        if provider == settings.llm_provider and settings.llm_base_url and "11434" not in settings.llm_base_url and "responses" not in settings.llm_base_url:
            base = settings.llm_base_url          # explicit override
        return OpenAICompatLLM(provider, model or "default", base, key, rpm)
    if provider in ("openai", "openai-compatible"):
        return OpenAICompatLLM("openai", model or "default", settings.llm_base_url, settings.llm_api_key, rpm)
    raise LLMError(f"unknown LLM provider '{provider}'")


_cache: dict[tuple, LLM] = {}


def get_model_llm(provider: str, model: str | None, settings) -> LLM:
    key = (provider, model)
    if key in _cache:
        return _cache[key]
    llm = _build(provider, model, settings)
    _cache[key] = llm
    return llm


def get_llm(settings) -> LLM:
    key = (settings.llm_provider, settings.llm_model, settings.llm_base_url, os.environ.get("CODEWORLD_LLM_FALLBACKS", ""))
    if key in _cache:
        return _cache[key]
    llm: LLM = NullLLM()
    if settings.llm_provider != "none":
        try:
            parts = [_build(settings.llm_provider, settings.llm_model, settings)]
            for item in [x.strip() for x in os.environ.get("CODEWORLD_LLM_FALLBACKS", "").split(",") if x.strip()]:
                prov, _, model = item.partition(":")
                try:
                    parts.append(_build(prov, model, settings))
                except Exception as e:
                    log.warning("fallback %s skipped: %s", item, e)
            llm = parts[0] if len(parts) == 1 else ChainLLM(parts)
        except LLMError as e:
            log.warning("LLM disabled: %s", e)
    _cache[key] = llm
    return llm
