"""Streamlit demo UI.  Run:  streamlit run app.py"""
import json
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from agent import run_agent, save_run  # noqa: E402
from agent.core import PROVIDERS, build_fallbacks, default_model, make_client  # noqa: E402
from agent.tools import ROOT  # noqa: E402

PROVIDER = "gemini"
SECTION_NAMES = {"mdna": "Management's Discussion & Analysis", "risk_factors": "Risk Factors"}

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
        key_env = PROVIDERS[PROVIDER][1]
        api_key = os.getenv(key_env, "")  # read from .env only; never shown in the UI
        model = st.text_input("Model", value=default_model(PROVIDER))
    else:
        if not saved_traces:
            st.warning("No saved runs yet. Do a live run first (or `python cli.py AAPL`).")
        replay_file = st.selectbox("Saved run", saved_traces, format_func=lambda p: p.stem.replace("_trace", ""))
        replay_speed = st.slider("Seconds per step", 0.2, 3.0, 1.0, 0.1)


# ----------------------------------------------------------------------------- Event rendering
def _describe_call(name, args):
    """Plain-English description of a tool call (no function names or code)."""
    ticker = (args.get("ticker") or "").upper()
    if name == "get_company_facts":
        return f"Retrieving audited financial statements{f' for {ticker}' if ticker else ''} from SEC filings"
    if name == "get_filing_section":
        section = SECTION_NAMES.get(args.get("section", "mdna"), "the annual report")
        return f"Reading the {section} section of the latest 10-K{f' for {ticker}' if ticker else ''}"
    if name == "run_python":
        return "Running financial calculations"
    return "Gathering information"


def _show_result(name, output):
    try:
        data = json.loads(output)
    except Exception:
        st.text(output[:3000])
        return
    if isinstance(data, dict) and data.get("error"):
        st.warning("This step could not be completed. The agent will adjust and continue.")
        return

    if name == "get_company_facts":
        rows = {k.replace("_", " ").title(): v["values"] for k, v in data.get("metrics", {}).items()}
        if rows:
            df = pd.DataFrame(rows).T
            df.columns = [f"FY ending {c}" for c in df.columns]
            st.caption(f"{data.get('company', '')} · values in USD as reported in 10-K filings")
            st.dataframe(df.style.format(lambda v: "—" if pd.isna(v) else f"{v:,.2f}" if abs(v) < 1000
                                         else f"{v:,.0f}"))
        if data.get("missing_metrics"):
            st.caption("Not reported: " + ", ".join(m.replace("_", " ") for m in data["missing_metrics"]))
    elif name == "get_filing_section":
        st.caption(f"{data.get('company', '')} · Form {data.get('form', '10-K')} · "
                   f"period ending {data.get('report_date', '?')} · filed {data.get('filing_date', '?')}")
        if data.get("url"):
            st.markdown(f"[View the filing on SEC.gov]({data['url']})")
        st.markdown(f"> {data.get('text', '')[:1200]}…")
    elif name == "run_python":
        if data.get("ok") and data.get("stdout", "").strip():
            st.text(data["stdout"].strip())
        elif not data.get("ok"):
            st.warning("A calculation could not be completed. The agent will adjust and continue.")


def render_event(box, ev):
    with box:
        if ev["type"] == "notice":
            st.caption(f"⏳ {ev['text']}")
        elif ev["type"] == "thinking_text":
            st.markdown(f"**Analyst note:** {ev['text']}")
        elif ev["type"] == "tool_call":
            st.markdown(f"**Step {ev['step']} · {_describe_call(ev['name'], ev.get('input') or {})}**")
        elif ev["type"] == "tool_result":
            label = {"get_company_facts": "View financial data", "get_filing_section": "View filing excerpt",
                     "run_python": "View calculation results"}.get(ev["name"], "View details")
            with st.expander(label, expanded=False):
                _show_result(ev["name"], ev["output"])


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
        st.subheader("Agent reasoning & research steps")
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
    if not api_key:
        st.error(f"No Gemini API key found. Add {key_env} to your .env file and restart the app.")
        st.stop()
    client = make_client(PROVIDER, api_key=api_key)

    question = f"Analyze {ticker}. Focus: {focus or 'General fundamental health check.'}"
    st.subheader("Agent reasoning & research steps")
    box = st.container()

    try:
        with st.spinner("Agent is working..."):
            result = run_agent(question, client=client, model=model, provider=PROVIDER,
                               on_event=lambda ev: render_event(box, ev),
                               fallbacks=build_fallbacks(PROVIDER))
    except Exception as e:  # show a friendly message instead of a stack trace during the demo
        st.error(f"{type(e).__name__}: {e}")
        st.info("Tip: switch the sidebar to **Replay a saved run** to show a previous run.")
        st.stop()

    path = save_run(ticker, question, result)
    show_memo(result["memo"],
              f"{result['steps']} agent steps · {result['usage']['input_tokens']:,} input / "
              f"{result['usage']['output_tokens']:,} output tokens · saved to {path.name}",
              path.name)
