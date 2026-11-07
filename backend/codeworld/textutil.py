"""Identifier-aware tokenisation shared by BM25, the hashing embedder and glossary extraction."""
from __future__ import annotations
import re

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
STOP = frozenset("""a an the of to in on for and or is are be it this that with as at by from if then else self cls
return def class import not none true false null var let const function new do does how what where which who when why
can i you we they my our me about into use used using code file files please show tell explain find get""".split())


def split_identifier(s: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[_\W]+", s):
        if part:
            out.extend(_CAMEL.split(part))
    return [x.lower() for x in out if x]


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    for suf in ("ation", "ing", "ed", "es"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str, keep_full_identifiers: bool = True) -> list[str]:
    out: list[str] = []
    for m in _WORD.finditer(text):
        w = m.group(0)
        subs = [stem(x) for x in split_identifier(w) if x not in STOP and len(x) > 1]
        out.extend(subs)
        if keep_full_identifiers and len(subs) > 1:
            out.append(w.lower())
    return out


def fts_text(text: str) -> str:
    return " ".join(tokens(text))


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_SECRET_RE = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
]


def redact_secrets(text: str) -> str:
    """Redact API keys, tokens, and hardcoded credentials before sending to hosted LLMs."""
    if not text:
        return text

    def _cred_sub(m):
        return f"{m.group(1)}='[REDACTED_SECRET]'"

    text = re.sub(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']", _cred_sub, text)
    for pat in _SECRET_RE:
        text = pat.sub("[REDACTED_SECRET]", text)
    return text

