"""Streamlit demo UI.  Run:  streamlit run app.py"""
import json
import os
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from agent import run_agent, save_run  # noqa: E402
from agent.core import DEFAULT_PROVIDER, PROVIDERS, build_fallbacks, default_model, make_client  # noqa: E402
from agent.tools import ROOT  # noqa: E402

st.set_page_config(page_title="SEC Filing Analyst Agent", page_icon="📊", layout="wide")
st.title("📊 SEC Filing Analyst Agent")
st.caption("An AI agent that reads SEC filings, computes financial ratios in code, "
           "and writes an evidence-backed analyst memo. Not investment advice.")

saved_traces = sorted((ROOT / "outputs").glob("*_trace.json"), reverse=True)

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Mode", ["Live (call the AI)", "Replay a saved run"],
                    help="Replay re-plays a previous run step by step without calling any API. "
                         "Use it as a backup if you hit rate limits during the demo.")
    if mode.startswith("Live"):
        names = list(PROVIDERS)
        provider = st.selectbox("LLM provider", names, index=names.index(DEFAULT_PROVIDER),
                                help="gemini, groq, openrouter, and ollama have free options")
        key_env = PROVIDERS[provider][1]
        api_key = (st.text_input(f"API key ({key_env})", value=os.getenv(key_env, ""), type="password")
                   if key_env else "")
        model = st.text_input("Model", value=default_model(provider) if provider == DEFAULT_PROVIDER
                              else PROVIDERS[provider][2])
        st.caption("If rate-limited, the agent waits and retries automatically, then falls back to "
                   "other models/providers you have keys for.")
    else:
        if not saved_traces:
            st.warning("No saved runs yet. Do a live run first (or `python cli.py AAPL`).")
        replay_file = st.selectbox("Saved run", saved_traces, format_func=lambda p: p.stem.replace("_trace", ""))
        replay_speed = st.slider("Seconds per step", 0.2, 3.0, 1.0, 0.1)
    st.markdown("**Agent tools**")
    st.markdown("- `get_company_facts`: SEC XBRL financials\n"
                "- `get_filing_section`: 10-K MD&A / Risk Factors\n"
                "- `run_python`: sandboxed calculations")


def render_event(box, ev):
    with box:
        if ev["type"] == "notice":
            st.warning(f"⏳ {ev['text']}")
        elif ev["type"] == "thinking_text":
            st.info(f"**Step {ev['step']}:** {ev['text']}")
        elif ev["type"] == "tool_call":
            with st.expander(f"🔧 Step {ev['step']}: `{ev['name']}`", expanded=ev["name"] == "run_python"):
                if ev["name"] == "run_python":
                    st.code(ev["input"].get("code", ""), language="python")
                else:
                    st.json(ev["input"])
        elif ev["type"] == "tool_result":
            with st.expander(f"✅ Result from `{ev['name']}`", expanded=False):
                try:
                    st.json(json.loads(ev["output"]))
                except Exception:
                    st.text(ev["output"][:5000])


def show_memo(memo, caption, file_name):
    st.subheader("Analyst memo")
    st.markdown(memo)
    st.caption(caption)
    st.download_button("Download memo (.md)", memo, file_name=file_name)


# ----------------------------------------------------------------------------- Replay mode
if mode.startswith("Replay"):
    if saved_traces and st.button("Replay run", type="primary"):
        data = json.loads(Path(replay_file).read_text(encoding="utf-8"))
        st.markdown(f"**Question:** {data['question']}")
        st.subheader("Agent reasoning & tool calls")
        box = st.container()
        memo = ""
        for ev in data["trace"]:
            if ev["type"] == "final":
                memo = ev["text"]
                continue
            render_event(box, ev)
            if ev["type"] in ("tool_call", "thinking_text"):
                time.sleep(replay_speed)
        u = data.get("usage", {})
        show_memo(memo, f"Replayed from {Path(replay_file).name} · {u.get('input_tokens', 0):,} input / "
                        f"{u.get('output_tokens', 0):,} output tokens in the original run",
                  Path(replay_file).name.replace("_trace.json", "_memo.md"))
    st.stop()

# ----------------------------------------------------------------------------- Live mode
col1, col2 = st.columns([1, 3])
ticker = col1.text_input("Ticker", value="AAPL").strip().upper()
focus = col2.text_input("Focus question (optional)",
                        value="Is earnings quality improving or deteriorating over the last 3 years?")

run_clicked = st.button("Run analysis", type="primary")
st.caption("Or try a preset:")
presets = st.columns(4)
for i, (t, q) in enumerate([("AAPL", "Earnings quality trend"), ("MSFT", "Margin drivers"),
                            ("T", "Leverage and debt risk"), ("NVDA", "Growth sustainability")]):
    if presets[i].button(f"{t}: {q}"):
        ticker, focus, run_clicked = t, q, True

if run_clicked and ticker:
    if key_env and not api_key:
        st.error(f"Add your {provider} API key in the sidebar.")
        st.stop()
    if key_env:
        os.environ[key_env] = api_key
    client = make_client(provider, api_key=api_key)

    question = f"Analyze {ticker}. Focus: {focus or 'General fundamental health check.'}"
    st.subheader("Agent reasoning & tool calls")
    box = st.container()

    try:
        with st.spinner("Agent is working..."):
            result = run_agent(question, client=client, model=model, provider=provider,
                               on_event=lambda ev: render_event(box, ev),
                               fallbacks=None if provider == "anthropic" else build_fallbacks(provider))
    except Exception as e:  # show a friendly message instead of a stack trace during the demo
        st.error(f"{type(e).__name__}: {e}")
        st.info("Tip: switch the sidebar to **Replay a saved run** to show a previous run.")
        st.stop()

    path = save_run(ticker, question, result)
    show_memo(result["memo"],
              f"{result['steps']} agent steps · {result['usage']['input_tokens']:,} input / "
              f"{result['usage']['output_tokens']:,} output tokens · saved to {path.name}",
              path.name)
