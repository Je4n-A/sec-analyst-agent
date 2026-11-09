"""Rate-limit handling for free-tier LLM APIs.

Three layers of defense:
  1. Pacing: wait a minimum interval between LLM calls (free tiers cap requests/minute).
  2. Retry with backoff: on HTTP 429, wait (using the server's suggested delay when given) and retry.
  3. Fallback chain: if a model stays rate-limited, or its daily quota is used up, switch to the
     next model/provider (e.g. gemini-2.5-flash -> gemini-2.5-flash-lite -> groq) and keep going.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

DEFAULT_MIN_INTERVAL = {"gemini": 4.0, "groq": 2.0, "openrouter": 3.0, "ollama": 0.0, "anthropic": 0.0}
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "4"))
MAX_WAIT = 65.0  # seconds; per-minute windows reset within ~60s

_DELAY_PATTERNS = [
    r'retryDelay["\']?\s*[:=]\s*["\']?(\d+(?:\.\d+)?)s',  # Gemini: "retryDelay": "17s"
    r"try again in\s+(?:(\d+)m)?\s*(\d+(?:\.\d+)?)s",      # Groq: "Please try again in 1m7.5s" / "7.5s"
    r"retry (?:in|after)\s+(\d+(?:\.\d+)?)\s*s",            # generic
]


def is_rate_limit(err: Exception) -> bool:
    status = getattr(err, "status_code", None) or getattr(getattr(err, "response", None), "status_code", None)
    text = str(err).lower()
    return status == 429 or "rate limit" in text or "resource_exhausted" in text or "quota" in text


def is_daily_quota(err: Exception) -> bool:
    """Daily caps won't reset by waiting a minute, so skip straight to the fallback."""
    text = str(err).lower().replace("_", "")
    return any(s in text for s in ("perday", "per day", "requests per day", "rpd", "tokens per day", "tpd"))


def suggested_delay(err: Exception) -> float | None:
    text = str(err)
    headers = getattr(getattr(err, "response", None), "headers", None) or {}
    try:
        if headers.get("retry-after"):
            return float(headers["retry-after"])
    except (TypeError, ValueError):
        pass
    m = re.search(_DELAY_PATTERNS[0], text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(_DELAY_PATTERNS[1], text, re.I)
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    m = re.search(_DELAY_PATTERNS[2], text, re.I)
    return float(m.group(1)) if m else None


@dataclass
class Candidate:
    provider: str
    model: str
    client: object


@dataclass
class ResilientCaller:
    """Calls chat.completions.create across a chain of candidates with pacing + retries."""
    candidates: list[Candidate]
    emit: Callable[[dict], None] = lambda e: None
    sleep: Callable[[float], None] = time.sleep
    index: int = 0
    _last_call: float = field(default=0.0)

    @property
    def current(self) -> Candidate:
        return self.candidates[self.index]

    def _pace(self):
        env = os.getenv("LLM_MIN_INTERVAL")
        interval = float(env) if env else DEFAULT_MIN_INTERVAL.get(self.current.provider, 0.0)
        wait = interval - (time.time() - self._last_call)
        if wait > 0 and self._last_call:
            self.sleep(wait)

    def create(self, **kwargs):
        while True:
            cand = self.current
            for attempt in range(MAX_RETRIES + 1):
                self._pace()
                try:
                    self._last_call = time.time()
                    return cand.client.chat.completions.create(model=cand.model, **kwargs)
                except Exception as err:  # noqa: BLE001
                    if not is_rate_limit(err):
                        raise
                    if is_daily_quota(err) or attempt == MAX_RETRIES:
                        break
                    delay = suggested_delay(err) or min(5 * 2 ** attempt, MAX_WAIT)
                    delay = min(delay + 1, MAX_WAIT)
                    self.emit({"type": "notice", "text": f"Rate limited on {cand.provider}/{cand.model}. "
                                                         f"Waiting {delay:.0f}s, then retrying "
                                                         f"({attempt + 1}/{MAX_RETRIES})."})
                    self.sleep(delay)
            if self.index + 1 >= len(self.candidates):
                raise RuntimeError("All models are rate-limited. Wait a few minutes, add a fallback provider "
                                   "key (e.g. GROQ_API_KEY), or use Replay mode in the app.")
            self.index += 1
            nxt = self.current
            self.emit({"type": "notice", "text": f"Switching from {cand.provider}/{cand.model} "
                                                 f"to fallback {nxt.provider}/{nxt.model}."})
