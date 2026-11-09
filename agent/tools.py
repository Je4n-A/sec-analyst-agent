"""Tools the SEC Filing Analyst agent can call.

All data comes from free, public SEC EDGAR endpoints:
  - https://www.sec.gov/files/company_tickers.json        (ticker -> CIK)
  - https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json  (XBRL financials)
  - https://data.sec.gov/submissions/CIK##########.json   (filing index)
  - https://www.sec.gov/Archives/edgar/data/...            (filing documents)

The SEC requires a User-Agent with your name + email, and allows at most 10 requests/sec.
HTTP responses are cached in .cache/ so a rehearsal run makes the live demo fast and
resilient to network problems.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
WORK_DIR = ROOT / ".work"
CACHE_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)

USER_AGENT = os.getenv("SEC_USER_AGENT", "Student Project your_email@example.com")

# ---------------------------------------------------------------------------
# HTTP with caching
# ---------------------------------------------------------------------------
_last_call = 0.0


def _get(url: str, as_json: bool = True):
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE_DIR / (key + (".json" if as_json else ".txt"))
    if path.exists():
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if as_json else text

    global _last_call
    wait = 0.15 - (time.time() - _last_call)  # stay well under 10 req/sec
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    _last_call = time.time()
    resp.raise_for_status()
    path.write_text(resp.text, encoding="utf-8")
    return resp.json() if as_json else resp.text


def ticker_to_cik(ticker: str) -> tuple[str, str]:
    """Return (10-digit CIK, company name) for a ticker."""
    data = _get("https://www.sec.gov/files/company_tickers.json")
    t = ticker.upper().strip()
    for row in data.values():
        if row["ticker"].upper() == t:
            return str(row["cik_str"]).zfill(10), row["title"]
    raise ValueError(f"Ticker '{ticker}' not found in SEC company list.")


# ---------------------------------------------------------------------------
# Tool 1: structured financials from XBRL
# ---------------------------------------------------------------------------
# Each metric lists candidate US-GAAP tags in priority order (companies use different tags).
METRICS: dict[str, tuple[str, list[str]]] = {
    # name: (kind, [tags])  kind = "flow" (income/cash-flow, ~12-month duration) or "instant" (balance sheet)
    "revenue": ("flow", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                         "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]),
    "cost_of_revenue": ("flow", ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"]),
    "gross_profit": ("flow", ["GrossProfit"]),
    "operating_income": ("flow", ["OperatingIncomeLoss"]),
    "net_income": ("flow", ["NetIncomeLoss", "ProfitLoss"]),
    "eps_diluted": ("flow", ["EarningsPerShareDiluted"]),
    "interest_expense": ("flow", ["InterestExpense", "InterestExpenseNonoperating"]),
    "operating_cash_flow": ("flow", ["NetCashProvidedByUsedInOperatingActivities"]),
    "capex": ("flow", ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    "total_assets": ("instant", ["Assets"]),
    "current_assets": ("instant", ["AssetsCurrent"]),
    "current_liabilities": ("instant", ["LiabilitiesCurrent"]),
    "total_liabilities": ("instant", ["Liabilities"]),
    "stockholders_equity": ("instant", ["StockholdersEquity",
                                        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    "cash": ("instant", ["CashAndCashEquivalentsAtCarryingValue"]),
    "accounts_receivable": ("instant", ["AccountsReceivableNetCurrent"]),
    "inventory": ("instant", ["InventoryNet"]),
    "long_term_debt": ("instant", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    "short_term_debt": ("instant", ["LongTermDebtCurrent", "DebtCurrent", "CommercialPaper"]),
}


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _annual_values(units: dict, kind: str) -> tuple[str, dict[str, float]]:
    """Pick annual (10-K) values, keyed by period end date. Latest filing wins."""
    unit = "USD" if "USD" in units else ("USD/shares" if "USD/shares" in units else next(iter(units)))
    out: dict[str, tuple[str, float]] = {}
    for f in units[unit]:
        if f.get("form") not in ("10-K", "10-K/A"):
            continue
        if kind == "flow":
            if "start" not in f or not (350 <= _days(f["start"], f["end"]) <= 380):
                continue
        prev = out.get(f["end"])
        if prev is None or f.get("filed", "") > prev[0]:
            out[f["end"]] = (f.get("filed", ""), f["val"])
    return unit, {end: v for end, (_, v) in out.items()}


def extract_annual_metrics(company_facts: dict, years: int = 4) -> dict:
    gaap = company_facts.get("facts", {}).get("us-gaap", {})
    metrics: dict[str, dict] = {}
    # Fiscal year ends come from the revenue/net-income series (most reliable).
    for name, (kind, tags) in METRICS.items():
        merged: dict[str, float] = {}
        used_tags, unit = [], None
        for tag in tags:
            if tag not in gaap:
                continue
            u, vals = _annual_values(gaap[tag]["units"], kind)
            new = {k: v for k, v in vals.items() if k not in merged}
            if new:
                merged.update(new)
                used_tags.append(tag)
                unit = unit or u
        if merged:
            metrics[name] = {"tags": used_tags, "unit": unit, "values": merged}

    anchor = metrics.get("revenue") or metrics.get("net_income")
    if not anchor:
        raise ValueError("No annual revenue or net income found in XBRL data.")
    fy_ends = sorted(anchor["values"])[-years:]

    result = {}
    for name, m in metrics.items():
        vals = {fy: m["values"].get(fy) for fy in fy_ends}
        result[name] = {"tag": " | ".join(m["tags"]), "unit": m["unit"], "values": vals}
    missing = [n for n in METRICS if n not in result or all(v is None for v in result[n]["values"].values())]
    return {"fiscal_year_ends": fy_ends, "metrics": result, "missing_metrics": missing}


def get_company_facts(ticker: str, years: int = 4) -> dict:
    cik, name = ticker_to_cik(ticker)
    raw = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    data = extract_annual_metrics(raw, years=years)
    data.update({"company": raw.get("entityName", name), "ticker": ticker.upper(), "cik": cik,
                 "source": "SEC XBRL companyfacts API (annual 10-K values)"})
    # Save for run_python
    flat = {k: {"tag": v["tag"], "unit": v["unit"], "values": v["values"]} for k, v in data["metrics"].items()}
    (WORK_DIR / "facts.json").write_text(json.dumps(flat, indent=2))
    return data


# ---------------------------------------------------------------------------
# Tool 2: narrative sections from the latest 10-K
# ---------------------------------------------------------------------------
SECTION_PATTERNS = {
    "mdna": (r"item\s*7\.?\s*[\-–—:]?\s*management[’'`s]*\s+discussion", r"item\s*7a\.?|item\s*8\.?\s*[\-–—:]?\s*financial\s+statements"),
    "risk_factors": (r"item\s*1a\.?\s*[\-–—:]?\s*risk\s+factors", r"item\s*1b\.?|item\s*2\.?\s*[\-–—:]?\s*properties"),
}


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ")
    text = re.sub(r"\xa0", " ", text)
    return re.sub(r"\s+", " ", text)


def extract_section(text: str, section: str) -> str:
    """Return the longest start->end span (skips the table-of-contents match)."""
    start_pat, end_pat = SECTION_PATTERNS[section]
    best = ""
    for m in re.finditer(start_pat, text, flags=re.I):
        end = re.search(end_pat, text[m.end():], flags=re.I)
        chunk = text[m.start(): m.end() + end.start()] if end else text[m.start(): m.start() + 60000]
        if len(chunk) > len(best):
            best = chunk
    return best.strip()


def latest_10k(cik: str) -> dict:
    subs = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = subs["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form == "10-K":
            acc = recent["accessionNumber"][i]
            return {
                "accession": acc,
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{recent['primaryDocument'][i]}",
            }
    raise ValueError("No 10-K found in recent filings.")


MAX_SECTION_CHARS = int(os.getenv("MAX_SECTION_CHARS", "9000"))  # ~2,300 tokens; keeps free-tier TPM low


def get_filing_section(ticker: str, section: str = "mdna", max_chars: int = MAX_SECTION_CHARS) -> dict:
    if section not in SECTION_PATTERNS:
        raise ValueError(f"section must be one of {list(SECTION_PATTERNS)}")
    cik, name = ticker_to_cik(ticker)
    filing = latest_10k(cik)
    text = html_to_text(_get(filing["url"], as_json=False))
    body = extract_section(text, section)
    truncated = len(body) > max_chars
    return {"company": name, "form": "10-K", **filing, "section": section,
            "truncated": truncated, "chars": len(body),
            "text": body[:max_chars] if body else "Section not found in document."}


# ---------------------------------------------------------------------------
# Tool 3: sandboxed Python for all math
# ---------------------------------------------------------------------------
PRELUDE = """
import json, math
import pandas as pd
pd.set_option('display.width', 200); pd.set_option('display.max_columns', 20)
try:
    facts = json.load(open(r'{facts}'))
except FileNotFoundError:
    facts = {{}}
"""


def run_python(code: str, timeout: int = 30) -> dict:
    """Run code in a separate Python process (isolated, time-limited)."""
    script = PRELUDE.format(facts=WORK_DIR / "facts.json") + "\n" + code
    with tempfile.NamedTemporaryFile("w", suffix=".py", dir=WORK_DIR, delete=False) as f:
        f.write(script)
        path = f.name
    try:
        proc = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True,
                              timeout=timeout, cwd=WORK_DIR)
        return {"stdout": proc.stdout[-12000:], "stderr": proc.stderr[-4000:], "ok": proc.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {timeout}s", "ok": False}
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Tool schemas (what the LLM sees) + dispatcher
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "name": "get_company_facts",
        "description": "Get annual financial statement data (income statement, balance sheet, cash flow) "
                       "for a U.S. public company from SEC XBRL filings. Returns values by fiscal-year-end date, "
                       "the XBRL tag used for each metric, and a list of missing metrics. Also saves the data "
                       "as `facts` for run_python.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
                "years": {"type": "integer", "description": "Number of fiscal years (default 4)", "default": 4},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_filing_section",
        "description": "Get narrative text from the company's most recent 10-K: 'mdna' (Item 7, Management's "
                       "Discussion & Analysis) or 'risk_factors' (Item 1A). Use to explain why numbers changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "section": {"type": "string", "enum": ["mdna", "risk_factors"]},
            },
            "required": ["ticker", "section"],
        },
    },
    {
        "name": "run_python",
        "description": "Execute Python code for ALL calculations. Preloaded: `facts` dict "
                       "(metric -> {tag, unit, values: {fiscal_year_end: value}}), `pd` (pandas), `math`. "
                       "Values may be None if missing. Print results; only stdout is returned.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python code to run"}},
            "required": ["code"],
        },
    },
]

TOOL_FUNCS = {
    "get_company_facts": get_company_facts,
    "get_filing_section": get_filing_section,
    "run_python": run_python,
}


def call_tool(name: str, args: dict) -> str:
    try:
        result = TOOL_FUNCS[name](**args)
    except Exception as e:  # return errors to the model so it can recover
        result = {"error": f"{type(e).__name__}: {e}"}
    return json.dumps(result, default=str)
