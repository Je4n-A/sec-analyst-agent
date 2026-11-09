# SEC Filing Analyst Agent

An AI agent that does the first pass of fundamental equity research. You give it a stock ticker and an optional focus question. It **pulls the company's financial data from SEC filings, calculates the ratios in Python, reads the 10-K's MD&A to explain the trends, and writes a cited analyst memo**.

> Purpose: cut the first-pass analysis of a company's financial statements from hours to about a minute, while keeping every number traceable to a primary source. The agent gives analysis, not investment advice.

---

## How it works

```
 User: "Analyze AAPL. Focus: earnings quality"
            │
            ▼
 ┌──────────────────────┐   tool calls    ┌──────────────────────────────┐
 │  LLM (Claude)        │ ──────────────► │ get_company_facts  (SEC XBRL)│
 │  + system prompt     │                 │ get_filing_section (10-K)    │
 │  (role, workflow,    │ ◄────────────── │ run_python (sandboxed math)  │
 │   rules, format)     │   tool results  └──────────────────────────────┘
 └──────────────────────┘
            │  loop until done (max 15 steps)
            ▼
   Analyst memo (Markdown) + full trace saved to outputs/
```

1. **Reason:** the LLM reads the system prompt and decides which tool to call next.
2. **Act:** the Python loop runs that tool and sends the result back to the LLM.
3. **Repeat** until the LLM has enough evidence, then it writes the memo.

### Tools
| Tool | Data source | What it returns |
|---|---|---|
| `get_company_facts` | SEC XBRL `companyfacts` API | Annual revenue, margins inputs, cash flow, balance sheet items. Each value comes with its XBRL tag, and the tool reports which metrics are missing. It handles companies that switched tags between years. |
| `get_filing_section` | Latest 10-K document on EDGAR | Text of Item 7 (MD&A) or Item 1A (Risk Factors). It skips the table of contents. |
| `run_python` | Local, separate process | Runs the ratio math (margins, DuPont ROE, FCF, CFO/NI, growth). It's isolated and stops after 30 seconds. |

### Design choices worth mentioning
- **The LLM never does arithmetic in its head.** All numbers come from code, because LLMs make mistakes with mental math.
- **Every number cites its source:** the filing, the fiscal period, and the XBRL tag. Missing data is reported as missing, not estimated.
- **Guardrails:** no buy/sell recommendations, and facts are kept separate from interpretation.
- **Caching:** SEC responses are cached in `.cache/`, so after one rehearsal run the live demo is fast and doesn't depend on the network.
- **Traceability:** each run saves the memo and a JSON trace of every tool call in `outputs/`.

### Tech stack
Python · LLM tool/function calling (Google Gemini by default; Groq, OpenRouter, Ollama, or Claude also supported) · SEC EDGAR APIs · pandas · BeautifulSoup · Streamlit

---

## Setup (about 5 minutes)

```bash
cd sec-analyst-agent
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env: add your API key + name/email
```

### Choosing an LLM (free options available)
Set `LLM_PROVIDER` in `.env`:

| Provider | Cost | Get a key | Notes |
|---|---|---|---|
| `gemini` (default) | **Free tier**, no card | https://aistudio.google.com/apikey | Best free option: good tool calling, large context |
| `groq` | **Free tier** | https://console.groq.com/keys | Very fast; free tokens-per-minute cap is low for long 10-K text |
| `openrouter` | Some `:free` models | https://openrouter.ai/keys | Many models behind one key |
| `ollama` | Free, runs locally | https://ollama.com | No key; needs a decent PC (`ollama pull qwen3:8b`) |
| `anthropic` | Paid | https://console.anthropic.com | Claude models |

### Rate limits (free tiers)
Free APIs limit requests per minute and per day. The agent handles this in four ways (`agent/ratelimit.py`):
1. **Pacing:** it waits a few seconds between AI calls so it stays under the requests-per-minute limit.
2. **Automatic retry:** on a 429 error it waits for the delay the server suggests, then retries, up to 4 times.
3. **Fallback chain:** if a model is still throttled or its daily quota is used up, it switches to the next model, for example `gemini-3.5-flash-lite` → `gemini-3.8-flash` → Groq (if you added a Groq key), and keeps going in the same run.
4. **Fewer tokens:** older tool results are shortened before each call, the 10-K excerpts are capped, and the prompt asks for all the math in one Python call.

**Replay mode (demo backup):** each run is saved in `outputs/`. In the app sidebar, choose **Replay a saved run** to play a previous run back step by step with **no API calls**.

The agent loop supports both the OpenAI-compatible tool-calling format (Gemini, Groq, OpenRouter, Ollama) and Anthropic's format, so switching models only means changing one setting. You can also switch providers live in the Streamlit sidebar.

## Run

**Web demo (use this for the presentation):**
```bash
streamlit run app.py
```

**Command line:**
```bash
python cli.py AAPL "Is earnings quality improving or deteriorating?"
```

**Offline tests (no API key or internet needed; covers both API formats):**
```bash
python tests/test_offline.py
python tests/test_ratelimit.py
```

## Project structure
```
sec-analyst-agent/
├── prompts/system_prompt.md   # agent role, workflow, output format, rules
├── agent/
│   ├── core.py                # the agent loop (LLM <-> tools), provider support
│   ├── ratelimit.py           # pacing, 429 retry/backoff, model fallback chain
│   └── tools.py               # SEC EDGAR tools, Python sandbox, tool schemas
├── app.py                     # Streamlit demo UI
├── cli.py                     # command-line runner
├── tests/test_offline.py      # mocked-data tests for tools + agent loop
├── tests/test_ratelimit.py    # tests for retry, fallback, history trimming
├── outputs/                   # saved memos and tool traces
├── PRESENTATION.md            # 10-minute talk track + demo script
└── requirements.txt
```

## Limitations and next steps
- It uses annual 10-K data only. Next step: add 10-Q data for trailing-twelve-month figures.
- XBRL tags vary by company, so some metrics (such as debt for banks) may come back missing.
- Possible extensions: an earnings-call transcript tool, peer comparison, and a multi-agent bull/bear/PM debate.

## Links
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Gemini function calling: https://ai.google.dev/gemini-api/docs/function-calling
- Gemini OpenAI compatibility: https://ai.google.dev/gemini-api/docs/openai
- Anthropic tool use docs: https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview
- Streamlit: https://streamlit.io
