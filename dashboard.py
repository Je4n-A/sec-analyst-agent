"""Financial dashboard drawn straight from the SEC data the agent fetched.

No AI calls: every number comes from the `get_company_facts` tool result in the run's trace,
so the charts are always exact and also work in replay mode.
"""
import json

import altair as alt
import pandas as pd
import streamlit as st

# Validated categorical palette (first three slots: blue, orange, aqua), stepped per theme.
PALETTE = {"light": ["#2a78d6", "#eb6834", "#1baf7a"], "dark": ["#3987e5", "#d95926", "#199e70"]}


def facts_from_trace(trace: list[dict]) -> dict | None:
    """Return the most recent company-facts result in a run's trace, if any."""
    for ev in reversed(trace or []):
        if ev.get("type") == "tool_result" and ev.get("name") == "get_company_facts":
            try:
                data = json.loads(ev["output"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if isinstance(data, dict) and data.get("metrics"):
                return data
    return None


def _colors() -> list[str]:
    try:
        theme = st.context.theme.type or "light"
    except Exception:
        theme = "light"
    return PALETTE.get(theme, PALETTE["light"])


def _series(data: dict, name: str) -> dict:
    return (data["metrics"].get(name) or {}).get("values") or {}


def _frame(data: dict) -> pd.DataFrame:
    """One row per fiscal year, one column per metric (raw USD)."""
    years = data.get("fiscal_year_ends", [])
    df = pd.DataFrame({m: [_series(data, m).get(y) for y in years] for m in data["metrics"]}, index=years)
    df = df.apply(pd.to_numeric, errors="coerce")
    df.index = [f"FY{y[:4]}" for y in years]
    df.index.name = "Fiscal year"
    if {"long_term_debt", "short_term_debt"} & set(df):
        df["total_debt"] = df.reindex(columns=["long_term_debt", "short_term_debt"]).sum(axis=1, min_count=1)
    if {"revenue", "gross_profit"} <= set(df):
        df["gross_margin"] = df["gross_profit"] / df["revenue"] * 100
    if {"revenue", "operating_income"} <= set(df):
        df["operating_margin"] = df["operating_income"] / df["revenue"] * 100
    if {"revenue", "net_income"} <= set(df):
        df["net_margin"] = df["net_income"] / df["revenue"] * 100
    return df


def _scale(df: pd.DataFrame, cols: list[str]) -> tuple[float, str]:
    peak = df.reindex(columns=cols).abs().max().max()
    if pd.isna(peak) or peak >= 1e9:
        return 1e9, "B"
    return (1e6, "M") if peak >= 1e6 else (1, "")


def _money(v: float) -> str:
    if pd.isna(v):
        return "n/a"
    for div, suffix in ((1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            return f"${v / div:,.1f}{suffix}"
    return f"${v:,.0f}"


def _usable(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Columns that exist and have at least two years of data."""
    return [c for c in cols if c in df and df[c].notna().sum() >= 2]


def _long(df: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    out = df[list(cols)].rename(columns=cols).reset_index().melt(
        "Fiscal year", var_name="Series", value_name="value")
    return out.dropna(subset=["value"])


def _bar_chart(df: pd.DataFrame, cols: dict[str, str], title: str):
    cols = {c: lbl for c, lbl in cols.items() if c in _usable(df, list(cols))}
    if not cols:
        return None
    div, suffix = _scale(df, list(cols))
    long = _long(df, cols)
    long["amount"] = long["value"] / div
    long["Amount"] = long["value"].map(_money)
    order = list(cols.values())
    return (
        alt.Chart(long, title=title)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("Fiscal year:N", title=None, axis=alt.Axis(labelAngle=0),
                    scale=alt.Scale(paddingInner=0.25)),
            xOffset=alt.XOffset("Series:N", sort=order, scale=alt.Scale(paddingInner=0.08)),
            y=alt.Y("amount:Q", title=f"USD ({suffix})" if suffix else "USD",
                    axis=alt.Axis(grid=True, gridOpacity=0.4, tickCount=5)),
            color=alt.Color("Series:N", sort=order, title=None,
                            scale=alt.Scale(domain=order, range=_colors()[: len(order)]),
                            legend=alt.Legend(orient="top", direction="horizontal")),
            tooltip=["Fiscal year", "Series", "Amount"],
        )
        .properties(height=280)
    )


def _margin_chart(df: pd.DataFrame):
    labels = {"gross_margin": "Gross margin", "operating_margin": "Operating margin", "net_margin": "Net margin"}
    labels = {c: lbl for c, lbl in labels.items() if c in _usable(df, list(labels))}
    if not labels:
        return None
    long = _long(df, labels)
    long["Margin"] = long["value"].map(lambda v: f"{v:.1f}%")
    order = list(labels.values())
    color = alt.Color("Series:N", sort=order, title=None,
                      scale=alt.Scale(domain=order, range=_colors()[: len(order)]),
                      legend=alt.Legend(orient="top", direction="horizontal"))
    x = alt.X("Fiscal year:N", title=None, axis=alt.Axis(labelAngle=0))
    hover = alt.selection_point(fields=["Fiscal year"], on="pointerover", nearest=True, empty=False)

    base = alt.Chart(long, title="Profit margins").encode(x=x)
    lines = base.mark_line(strokeWidth=2).encode(
        y=alt.Y("value:Q", title="% of revenue", axis=alt.Axis(grid=True, gridOpacity=0.4, tickCount=5)),
        color=color)
    points = base.mark_point(filled=True, size=70, opacity=1).encode(y="value:Q", color=color)
    rule = (base.mark_rule(strokeWidth=1, opacity=0.5)
            .encode(opacity=alt.condition(hover, alt.value(0.5), alt.value(0)),
                    tooltip=["Fiscal year", "Series", "Margin"])
            .add_params(hover))
    hover_points = points.encode(
        size=alt.condition(hover, alt.value(120), alt.value(70)),
        tooltip=["Fiscal year", "Series", "Margin"])
    return (lines + hover_points + rule).properties(height=280)


def _kpis(df: pd.DataFrame):
    latest, prev = df.index[-1], df.index[-2] if len(df) > 1 else None
    tiles = []

    def change(col):
        if prev is None or col not in df or pd.isna(df.at[prev, col]) or not df.at[prev, col]:
            return None
        return (df.at[latest, col] / df.at[prev, col] - 1) * 100

    for col, label in (("revenue", "Revenue"), ("net_income", "Net income"),
                       ("operating_cash_flow", "Operating cash flow")):
        if col in df and pd.notna(df.at[latest, col]):
            ch = change(col)
            tiles.append((label, _money(df.at[latest, col]),
                          None if ch is None else f"{ch:+.1f}% vs {prev}"))
    if "net_margin" in df and pd.notna(df.at[latest, "net_margin"]):
        pts = (df.at[latest, "net_margin"] - df.at[prev, "net_margin"]
               if prev is not None and pd.notna(df.at[prev, "net_margin"]) else None)
        tiles.append(("Net margin", f"{df.at[latest, 'net_margin']:.1f}%",
                      None if pts is None else f"{pts:+.1f} pts vs {prev}"))
    return latest, tiles


def render_dashboard(data: dict | None):
    """Draw headline numbers and trend charts. Silently skips anything the company doesn't report."""
    if not data:
        return
    df = _frame(data)
    if df.empty:
        return

    st.subheader(f"Financial dashboard · {data.get('company', data.get('ticker', ''))}")
    latest, tiles = _kpis(df)
    if tiles:
        st.caption(f"Latest fiscal year: {latest} · figures from 10-K filings via SEC XBRL")
        for col, (label, value, delta) in zip(st.columns(len(tiles)), tiles):
            col.metric(label, value, delta, border=True)

    charts = [
        _bar_chart(df, {"revenue": "Revenue", "net_income": "Net income"}, "Revenue and net income"),
        _margin_chart(df),
        _bar_chart(df, {"operating_cash_flow": "Operating cash flow", "net_income": "Net income"},
                   "Cash flow vs. net income (earnings quality)"),
        _bar_chart(df, {"total_debt": "Total debt", "cash": "Cash"}, "Debt and cash"),
    ]
    charts = [c for c in charts if c is not None]
    for i in range(0, len(charts), 2):
        for col, chart in zip(st.columns(2), charts[i:i + 2]):
            col.altair_chart(chart, theme="streamlit")

    table_cols = {"revenue": "Revenue", "net_income": "Net income", "operating_cash_flow": "Operating cash flow",
                  "total_debt": "Total debt", "cash": "Cash", "gross_margin": "Gross margin %",
                  "operating_margin": "Operating margin %", "net_margin": "Net margin %"}
    shown = {c: lbl for c, lbl in table_cols.items() if c in df}
    with st.expander("View dashboard data as a table"):
        tbl = df[list(shown)].rename(columns=shown).T
        st.dataframe(tbl.style.format(
            lambda v: "n/a" if pd.isna(v) else (f"{v:.1f}%" if abs(v) < 1000 else _money(v))))
