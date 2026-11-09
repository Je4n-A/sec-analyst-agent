"""Offline tests: mocked SEC responses + a scripted fake LLM. Run:  python -m pytest -q  (or python tests/test_offline.py)"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import tools  # noqa: E402
from agent.core import run_agent  # noqa: E402


def _flow(vals, tag_years):
    return [{"start": f"{y-1}-10-01", "end": f"{y}-09-30", "val": v, "form": "10-K", "filed": f"{y}-11-01"}
            for y, v in zip(tag_years, vals)]


def _inst(vals, years):
    return [{"end": f"{y}-09-30", "val": v, "form": "10-K", "filed": f"{y}-11-01"} for y, v in zip(years, vals)]


YEARS = [2021, 2022, 2023, 2024]
FAKE_FACTS = {"entityName": "Demo Corp", "facts": {"us-gaap": {
    # tag switch mid-history to test merging
    "SalesRevenueNet": {"units": {"USD": _flow([300, 330], [2021, 2022])}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": _flow([360, 390], [2023, 2024])
        + [{"start": "2024-07-01", "end": "2024-09-30", "val": 99, "form": "10-K", "filed": "2024-11-01"}]}},  # quarterly: must be ignored
    "NetIncomeLoss": {"units": {"USD": _flow([60, 66, 70, 72], YEARS)}},
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": _flow([70, 72, 68, 65], YEARS)}},
    "Assets": {"units": {"USD": _inst([500, 520, 540, 560], YEARS)}},
    "StockholdersEquity": {"units": {"USD": _inst([200, 210, 215, 220], YEARS)}},
    "EarningsPerShareDiluted": {"units": {"USD/shares": _flow([1.0, 1.1, 1.2, 1.25], YEARS)}},
}}}

FAKE_HTML = ("<html><body><p>Table of contents Item 7. Management's Discussion and Analysis 25 "
             "Item 8. Financial Statements 40</p><p>Item 7. Management’s Discussion and Analysis of Financial "
             "Condition. Revenue grew due to strong services demand. Margins expanded.</p>"
             "<p>Item 7A. Quantitative and Qualitative Disclosures</p></body></html>")


def fake_get(url, as_json=True):
    if "company_tickers" in url:
        return {"0": {"cik_str": 12345, "ticker": "DEMO", "title": "Demo Corp"}}
    if "companyfacts" in url:
        return FAKE_FACTS
    if "submissions" in url:
        return {"filings": {"recent": {"form": ["8-K", "10-K"], "accessionNumber": ["x", "0000012345-24-000001"],
                                       "filingDate": ["2024-12-01", "2024-11-01"], "reportDate": ["", "2024-09-30"],
                                       "primaryDocument": ["a.htm", "demo-10k.htm"]}}}
    return FAKE_HTML


tools._get = fake_get


def test_company_facts():
    d = tools.get_company_facts("demo")
    rev = d["metrics"]["revenue"]["values"]
    assert list(rev.values()) == [300, 330, 360, 390], rev
    assert "SalesRevenueNet" in d["metrics"]["revenue"]["tag"]
    assert d["metrics"]["eps_diluted"]["unit"] == "USD/shares"
    assert "inventory" in d["missing_metrics"]


def test_filing_section():
    s = tools.get_filing_section("DEMO", "mdna")
    assert "services demand" in s["text"] and "Table of contents" not in s["text"], s["text"]
    assert s["url"].endswith("/12345/000001234524000001/demo-10k.htm")


def test_run_python_uses_facts():
    tools.get_company_facts("DEMO")
    r = tools.run_python("v=facts['net_income']['values']; print(round(sum(v.values()),1))")
    assert r["ok"] and r["stdout"].strip() == "268", r
    assert not tools.run_python("raise ValueError('x')")["ok"]


def test_run_python_preloads_df_and_years():
    tools.get_company_facts("DEMO")
    r = tools.run_python(
        "print(years)\n"
        "print(list(df.index) == years, df.loc[years[-1], 'revenue'], df['net_income'].sum())\n"
        "print('inventory' in df.columns)")
    assert r["ok"], r
    lines = r["stdout"].strip().splitlines()
    assert lines[0] == str([f"{y}-09-30" for y in YEARS]), lines
    assert lines[1].split() == ["True", "390", "268"], lines
    assert lines[2] == "False", lines  # metric missing from facts -> not a column
    # Variables don't persist, but every fresh call gets df/years again.
    r2 = tools.run_python("print(len(years), df.shape[0])")
    assert r2["ok"] and r2["stdout"].split() == ["4", "4"], r2


class FakeLLM:
    """Scripted model: call facts -> run python -> read MD&A -> answer."""
    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls += 1
        U = SimpleNamespace(input_tokens=100, output_tokens=50)
        tu = lambda i, n, a: SimpleNamespace(type="tool_use", id=f"t{i}", name=n, input=a)  # noqa: E731
        if self.calls == 1:
            return SimpleNamespace(stop_reason="tool_use", usage=U, content=[
                SimpleNamespace(type="text", text="Pulling financials first."), tu(1, "get_company_facts", {"ticker": "DEMO"})])
        if self.calls == 2:
            return SimpleNamespace(stop_reason="tool_use", usage=U, content=[tu(2, "run_python", {"code":
                "r=facts['revenue']['values']; n=facts['net_income']['values']\n"
                "for k in r: print(k, round(n[k]/r[k]*100,1))"})])
        if self.calls == 3:
            last = kw["messages"][-1]["content"][0]["content"]
            assert "2024-09-30 18.5" in last, last
            return SimpleNamespace(stop_reason="tool_use", usage=U, content=[tu(3, "get_filing_section", {"ticker": "DEMO", "section": "mdna"})])
        return SimpleNamespace(stop_reason="end_turn", usage=U, content=[SimpleNamespace(type="text", text="# Demo Corp memo")])


class FakeOpenAI:
    """Scripted OpenAI-compatible model (Gemini/Groq format): facts -> python -> answer."""
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        U = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        call = lambda i, n, a: SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))  # noqa: E731
        msg = lambda content=None, calls=None: SimpleNamespace(  # noqa: E731
            usage=U, choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])
        if self.calls == 1:
            assert kw["messages"][0]["role"] == "system" and kw["tools"][0]["type"] == "function"
            return msg("Getting data.", [call(1, "get_company_facts", {"ticker": "DEMO"})])
        if self.calls == 2:
            assert kw["messages"][-1]["role"] == "tool" and "Demo Corp" in kw["messages"][-1]["content"]
            return msg(None, [call(2, "run_python", {"code": "print(len(facts))"})])
        assert kw["messages"][-1]["content"].startswith('{"stdout": "')
        return msg("# Demo memo (gemini)")


def test_agent_loop_openai_compatible():
    events = []
    res = run_agent("Analyze DEMO", client=FakeOpenAI(), provider="gemini", model="x", on_event=events.append)
    assert res["memo"] == "# Demo memo (gemini)" and res["steps"] == 3, res
    assert [e["type"] for e in events][:2] == ["thinking_text", "tool_call"]
    assert res["usage"] == {"input_tokens": 300, "output_tokens": 150}


def test_agent_loop():
    events = []
    res = run_agent("Analyze DEMO", client=FakeLLM(), provider="anthropic", model="x", on_event=events.append)
    assert res["memo"] == "# Demo Corp memo" and res["steps"] == 4
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["get_company_facts", "run_python", "get_filing_section"]
    assert res["usage"]["input_tokens"] == 400


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
