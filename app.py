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
from dashboard import facts_from_trace, render_dashboard  # noqa: E402

PROVIDER = "gemini"
SECTION_NAMES = {"mdna": "Management's Discussion & Analysis", "risk_factors": "Risk Factors"}
# Models confirmed to work with the agent (tool calling via Gemini's OpenAI-compatible API).
GEMINI_MODELS = {
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite (fast, higher free quota)",
    "gemini-3.8-flash": "Gemini 3.8 Flash (stronger reasoning, lower free quota)",
}
REPLAY_TIP = " Switch the sidebar to **Replay a saved run** to show a previous run."


def friendly_error(e: Exception) -> str:
    """Turn API/network exceptions into a short, demo-friendly message."""
    status = getattr(e, "status_code", None)
    detail = f"{getattr(e, 'body', '')} {e}".lower()
    name = type(e).__name__
    if "api key" in detail or "api_key" in detail or status in (401, 403):
        return "Invalid API key. Check the Gemini key in your .env file, then restart the app."
    if status == 404 or "no longer available" in detail or "not found" in detail:
        return "Model unavailable. Pick a different model in the sidebar."
    if status == 429 or "rate-limited" in detail or "quota" in detail or "resource_exhausted" in detail:
        return "Daily quota reached for the available models." + REPLAY_TIP
    if "timeout" in name.lower() or "timed out" in detail:
        return "The AI service took too long to respond. Please try again."
    if "connection" in name.lower():
        return "Could not reach the AI service. Check your internet connection and try again."
    if status and status >= 500:
        return "The AI service is having problems right now. Please try again in a minute." + REPLAY_TIP
    if status == 400:
        return "The AI service rejected the request. Try again, or pick a different model in the sidebar."
    return "Something went wrong while running the analysis. Please try again." + REPLAY_TIP


def filing_from_trace(trace: list[dict]) -> dict | None:
    """Latest 10-K metadata (form, dates, URL) the agent read during the run, if any."""
    for ev in reversed(trace or []):
        if ev.get("type") == "tool_result" and ev.get("name") == "get_filing_section":
            try:
                data = json.loads(ev["output"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if isinstance(data, dict) and data.get("url"):
                return data
    return None


st.set_page_config(page_title="SEC Filing Analyst", page_icon="📈", layout="wide")
st.title("SEC Filing Analyst")
st.caption("AI-assisted fundamental analysis built on audited SEC filings. "
           "Every figure is sourced from 10-K data and every calculation is run in code.")

saved_traces = sorted((ROOT / "outputs").glob("*_trace.json"), reverse=True)

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Mode", ["Live (call the AI)", "Replay a saved run"],
                    help="Replay re-plays a previous run step by step without calling any API. "
                         "Use it as a backup if you hit rate limits during the demo.")
    if mode.startswith("Live"):
        key_env = PROVIDERS[PROVIDER][1]
        api_key = os.getenv(key_env, "")  # read from .env only; never shown in the UI
        models = list(dict.fromkeys([default_model(PROVIDER), *GEMINI_MODELS]))
        model = st.selectbox("Model", models, format_func=lambda m: GEMINI_MODELS.get(m, m))
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
    """Detailed view of one agent event (used in the Research steps tab)."""
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


def show_progress(status, ev):
    """One-line live progress update while the agent works."""
    if ev["type"] == "tool_call":
        text = _describe_call(ev["name"], ev.get("input") or {})
        status.update(label=f"{text}…")
        status.write(f"✓ {text}")
    elif ev["type"] == "notice":
        status.write(f"⏳ {ev['text']}")


# ----------------------------------------------------------------------------- Results layout
def render_banner(facts, filing, fallback_ticker=""):
    if not facts and not filing:
        return
    src = facts or filing
    company = src.get("company", "")
    ticker = (src.get("ticker") or fallback_ticker).upper()
    st.markdown(f"### {company}{f' ({ticker})' if ticker else ''}")
    parts = []
    if facts and facts.get("fiscal_year_ends"):
        parts.append(f"Latest fiscal year ended {facts['fiscal_year_ends'][-1]}")
    if filing:
        parts.append(f"Form {filing.get('form', '10-K')} filed {filing.get('filing_date', '')}")
        parts.append(f"[View 10-K on SEC.gov]({filing['url']})")
    elif facts and facts.get("cik"):
        parts.append(f"[Filings on SEC.gov](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                     f"&CIK={facts['cik']}&type=10-K)")
    st.caption(" · ".join(parts))


def render_results(question, trace, memo, details, file_name, ticker=""):
    facts, filing = facts_from_trace(trace), filing_from_trace(trace)
    render_banner(facts, filing, ticker)
    tab_dash, tab_memo, tab_steps = st.tabs(["Dashboard", "Analyst memo", "Research steps"])
    with tab_dash:
        if facts:
            render_dashboard(facts)
        else:
            st.info("No financial statement data was retrieved in this run.")
    with tab_memo:
        st.markdown(memo)
        st.download_button("Download memo (.md)", memo, file_name=file_name)
        with st.expander("Run details"):
            st.markdown(f"**Question:** {question}")
            st.caption(" · ".join(details))
    with tab_steps:
        box = st.container()
        for ev in trace:
            if ev.get("type") != "final":
                render_event(box, ev)


def footer():
    st.divider()
    st.caption("Data: SEC EDGAR (XBRL financial data and 10-K filings). "
               "For educational purposes only. Not investment advice.")


def token_detail(usage):
    return f"{usage.get('input_tokens', 0):,} input / {usage.get('output_tokens', 0):,} output tokens"


# ----------------------------------------------------------------------------- Replay mode
if mode.startswith("Replay"):
    if saved_traces and st.button("Replay run", type="primary"):
        data = json.loads(Path(replay_file).read_text(encoding="utf-8"))
        trace = data["trace"]
        status = st.status("Replaying saved analysis…", expanded=True)
        for ev in trace:
            if ev["type"] in ("tool_call", "notice"):
                show_progress(status, ev)
                if ev["type"] == "tool_call":
                    time.sleep(replay_speed)
        status.update(label="Analysis complete", state="complete", expanded=False)
        memo = next((ev["text"] for ev in reversed(trace) if ev["type"] == "final"), "")
        render_results(data["question"], trace, memo,
                       [f"Replayed from {Path(replay_file).name}",
                        f"{token_detail(data.get('usage', {}))} in the original run"],
                       Path(replay_file).name.replace("_trace.json", "_memo.md"))
    footer()
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
    status = st.status(f"Analyzing {ticker}…", expanded=True)
    try:
        result = run_agent(question, client=client, model=model, provider=PROVIDER,
                           on_event=lambda ev: show_progress(status, ev),
                           fallbacks=[c for c in build_fallbacks(PROVIDER) if c.model != model])
    except Exception as e:  # show a friendly message instead of a stack trace during the demo
        status.update(label="Analysis stopped", state="error", expanded=False)
        st.error(friendly_error(e))
        with st.expander("Technical details"):
            st.code(f"{type(e).__name__}: {e}", language=None)
        st.stop()
    status.update(label="Analysis complete", state="complete", expanded=False)

    path = save_run(ticker, question, result)
    render_results(question, result["trace"], result["memo"],
                   [f"Model: {GEMINI_MODELS.get(model, model)}", f"{result['steps']} agent steps",
                    token_detail(result["usage"]), f"Saved to outputs/{path.name}"],
                   path.name, ticker)

footer()
