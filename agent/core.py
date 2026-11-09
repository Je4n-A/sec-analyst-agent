"""The agent loop: LLM <-> tools until the model produces a final answer.

This is the "ReAct"-style tool-use loop:
  1. Send the conversation + tool schemas to the LLM.
  2. If the LLM asks to use tools, run them and send the results back.
  3. Repeat until the LLM stops asking for tools; its last message is the memo.

Supported LLM providers (set LLM_PROVIDER in .env):
  gemini      Google Gemini: FREE tier, no credit card  (key: https://aistudio.google.com/apikey)
  groq        Groq: FREE tier, very fast, low tokens/min (key: https://console.groq.com/keys)
  openrouter  OpenRouter: some free ":free" models        (key: https://openrouter.ai/keys)
  ollama      Local models on your own PC, free, no key   (https://ollama.com)
  anthropic   Claude (paid API)                          (key: https://console.anthropic.com)
All except anthropic use the OpenAI-compatible Chat Completions format.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from .ratelimit import Candidate, ResilientCaller
from .tools import ROOT, TOOL_SCHEMAS, call_tool

SYSTEM_PROMPT = (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
MAX_STEPS = 15

PROVIDERS = {
    # name: (base_url, api-key env var, default model)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-2.5-flash"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai/gpt-oss-20b:free"),
    "ollama": ("http://localhost:11434/v1", None, "qwen3:8b"),
    "anthropic": (None, "ANTHROPIC_API_KEY", "claude-sonnet-5-5"),
}
DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()


def default_model(provider: str = DEFAULT_PROVIDER) -> str:
    return os.getenv("LLM_MODEL") or PROVIDERS[provider][2]


DEFAULT_MODEL = default_model()

Event = dict  # {"type": "tool_call"|"tool_result"|"thinking_text"|"final", ...}


def make_client(provider: str = DEFAULT_PROVIDER, api_key: str | None = None):
    base_url, key_env, _ = PROVIDERS[provider]
    key = api_key or (os.getenv(key_env) if key_env else None)
    if provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=key, max_retries=5)  # SDK retries 429s with backoff
    from openai import OpenAI
    # max_retries=0: our ResilientCaller handles retries, pacing and fallbacks itself
    return OpenAI(base_url=base_url, api_key=key or "ollama", max_retries=0, timeout=120)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_agent(
    user_message: str,
    client=None,
    model: str | None = None,
    provider: str = DEFAULT_PROVIDER,
    on_event: Callable[[Event], None] | None = None,
    max_steps: int = MAX_STEPS,
    fallbacks: list[Candidate] | None = None,
) -> dict:
    """Run the agent and return {"memo": str, "steps": int, "trace": [...], "usage": {...}}.

    fallbacks: models to switch to if the main one is rate-limited. Defaults to build_fallbacks()
    when no client is passed in.
    """
    emit = on_event or (lambda e: None)
    model = model or default_model(provider)
    if provider == "anthropic":
        return _loop_anthropic(user_message, client or make_client(provider), model, emit, max_steps)
    if fallbacks is None and client is None:
        fallbacks = build_fallbacks(provider)
    return _loop_openai(user_message, client or make_client(provider), model, emit, max_steps,
                        provider=provider, fallbacks=fallbacks)


def _record(trace, emit, ev):
    trace.append(ev)
    emit(ev)


def _finish(trace, emit, step, memo, usage):
    _record(trace, emit, {"type": "final", "step": step, "text": memo})
    return {"memo": memo, "steps": step, "trace": trace, "usage": usage}


# ---------------------------------------------------------------------------
# OpenAI-compatible loop (Gemini, Groq, OpenRouter, Ollama)
# ---------------------------------------------------------------------------
OPENAI_TOOLS = [{"type": "function",
                 "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                for t in TOOL_SCHEMAS]
OLD_TOOL_RESULT_CHARS = 1500  # older tool outputs are shortened before each call to save tokens


def build_fallbacks(provider: str) -> list[Candidate]:
    """Fallback chain from LLM_FALLBACKS (e.g. "gemini:gemini-2.5-flash-lite,groq"), or a sensible default
    that only includes providers you have a key for."""
    spec = os.getenv("LLM_FALLBACKS")
    if spec is None:
        parts = []
        if provider == "gemini":
            parts.append("gemini:gemini-2.5-flash-lite")
        for p in ("gemini", "groq", "openrouter"):
            if p != provider and os.getenv(PROVIDERS[p][1]):
                parts.append(p)
        spec = ",".join(parts)
    out = []
    for item in filter(None, (s.strip() for s in spec.split(","))):
        p, _, m = item.partition(":")
        if p in PROVIDERS and p != "anthropic":
            try:
                out.append(Candidate(p, m or PROVIDERS[p][2], make_client(p)))
            except Exception:  # noqa: BLE001  (missing package or key)
                continue
    return out


def _compact_history(messages: list[dict]) -> list[dict]:
    """Shorten tool results from earlier turns. The model already read them, and the full data is
    still available to run_python via `facts`. This sharply cuts tokens per minute."""
    last_assistant = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    out = []
    for i, m in enumerate(messages):
        if m["role"] == "tool" and i < last_assistant and len(m["content"]) > OLD_TOOL_RESULT_CHARS:
            m = {**m, "content": m["content"][:OLD_TOOL_RESULT_CHARS] +
                 " ...[truncated: already read earlier; use run_python with `facts` for exact numbers]"}
        out.append(m)
    return out


def _loop_openai(user_message, client, model, emit, max_steps, provider="gemini", fallbacks=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message}]
    trace, usage = [], {"input_tokens": 0, "output_tokens": 0}
    caller = ResilientCaller([Candidate(provider, model, client)] + list(fallbacks or []),
                             emit=lambda e: _record(trace, emit, e))

    for step in range(1, max_steps + 1):
        resp = caller.create(messages=_compact_history(messages), tools=OPENAI_TOOLS)
        if getattr(resp, "usage", None):
            usage["input_tokens"] += resp.usage.prompt_tokens or 0
            usage["output_tokens"] += resp.usage.completion_tokens or 0
        msg = resp.choices[0].message
        calls = msg.tool_calls or []

        if not calls:
            return _finish(trace, emit, step, (msg.content or "").strip(), usage)

        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                        for c in calls]})
        if msg.content and msg.content.strip():
            _record(trace, emit, {"type": "thinking_text", "step": step, "text": msg.content})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            _record(trace, emit, {"type": "tool_call", "step": step, "name": c.function.name, "input": args})
            output = call_tool(c.function.name, args)
            _record(trace, emit, {"type": "tool_result", "step": step, "name": c.function.name, "output": output})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": output})

    return _finish(trace, emit, max_steps, "Stopped: reached the maximum number of steps.", usage)


# ---------------------------------------------------------------------------
# Anthropic (Claude) loop
# ---------------------------------------------------------------------------
def _loop_anthropic(user_message, client, model, emit, max_steps):
    messages = [{"role": "user", "content": user_message}]
    trace, usage = [], {"input_tokens": 0, "output_tokens": 0}

    for step in range(1, max_steps + 1):
        resp = client.messages.create(model=model, max_tokens=4096, system=SYSTEM_PROMPT,
                                      tools=TOOL_SCHEMAS, messages=messages)
        usage["input_tokens"] += getattr(resp.usage, "input_tokens", 0)
        usage["output_tokens"] += getattr(resp.usage, "output_tokens", 0)
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            memo = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            return _finish(trace, emit, step, memo, usage)

        tool_results = []
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                _record(trace, emit, {"type": "thinking_text", "step": step, "text": block.text})
            elif block.type == "tool_use":
                _record(trace, emit, {"type": "tool_call", "step": step, "name": block.name, "input": block.input})
                output = call_tool(block.name, block.input)
                _record(trace, emit, {"type": "tool_result", "step": step, "name": block.name, "output": output})
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": tool_results})

    return _finish(trace, emit, max_steps, "Stopped: reached the maximum number of steps.", usage)


def save_run(ticker: str, question: str, result: dict) -> Path:
    """Save the memo + full tool trace to outputs/ (useful for the submission)."""
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    memo_path = out_dir / f"{ticker.upper()}_{stamp}_memo.md"
    memo_path.write_text(result["memo"], encoding="utf-8")
    (out_dir / f"{ticker.upper()}_{stamp}_trace.json").write_text(
        json.dumps({"question": question, "usage": result["usage"], "trace": result["trace"]},
                   indent=2, default=str), encoding="utf-8")
    return memo_path
