# SEC Filing Analyst Agent

An AI agent that does the first pass of fundamental equity research. You give it a stock ticker and an optional focus question. It **pulls the company's financial data from SEC filings, calculates the ratios in Python, reads the 10-K's MD&A to explain the trends, and writes a cited analyst memo**, shown alongside an interactive financial dashboard.

> Purpose: cut the first-pass analysis of a company's financial statements from hours to about a minute, while keeping every number traceable to a primary source. The agent gives analysis, not investment advice.

<!-- Screenshot: save a capture of the Dashboard tab as docs/dashboard.png, then uncomment:
![Dashboard tab for Apple Inc.](docs/dashboard.png)
-->

---

## What you get

Enter a ticker (or click a preset) and the app shows:

1. **Live progress:** a status box lists each research step as the agent works ("Retrieving audited financial statements…", "Reading the MD&A section…", "Running financial calculations…"), then collapses to **Analysis complete**.
2. **Company banner:** name, ticker, latest fiscal year, filing date, and a link to the 10-K on SEC.gov.
3. **Three tabs:**
   - **Dashboard:** headline tiles (revenue, net income, operating cash flow, net margin, each with year-over-year change) and four charts: revenue and net income, profit margins, cash flow vs. net income (earnings quality), and debt vs. cash. Hover for exact values, or open the data as a table.
   - **Analyst memo:** the agent's cited memo, downloadable as Markdown. Run details (model, steps, tokens) are tucked into a collapsed section.
   - **Research steps:** every step the agent took, in plain English, with the financial data it retrieved, its calculation results, and the 10-K excerpt it read.

The dashboard is drawn directly from the SEC data the agent fetched. It needs no extra AI calls, so its numbers are always exact and it works in replay mode too.

---

## How it works

```
 User: "Analyze AAPL. Focus: earnings quality"
            │
            ▼
 ┌──────────────────────┐   tool calls    ┌──────────────────────────────┐
 │  LLM (Gemini)        │ ──────────────► │ get_company_facts  (SEC XBRL)│
 │  + system prompt     │                 │ get_filing_section (10-K)    │
 │  (role, workflow,    │ ◄────────────── │ run_python (sandboxed math)  │
 │   rules, format)     │   tool results  └──────────────────────────────┘
 └──────────────────────┘
            │  loop until done (max 15 steps)
            ▼
   Analyst memo + dashboard; full trace saved to outputs/
```

1. **Reason:** the LLM reads the system prompt and decides which tool to call next.
2. **Act:** the Python loop runs that tool and sends the result back to the LLM.
3. **Repeat** until the LLM has enough evidence, then it writes the memo.

### Tools
| Tool | Data source | What it returns |
|---|---|---|
| `get_company_facts` | SEC XBRL `companyfacts` API | Annual revenue, margin inputs, cash flow, balance sheet items. Each value comes with its XBRL tag, and the tool reports which metrics are missing. It handles companies that switched tags between years. |
| `get_filing_section` | Latest 10-K document on EDGAR | Text of Item 7 (MD&A) or Item 1A (Risk Factors). It skips the table of contents. |
| `run_python` | Local, separate process | Runs the ratio math (margins, DuPont ROE, FCF, CFO/NI, growth). It's isolated and stops after 30 seconds. |

### Design choices worth mentioning
- **The LLM never does arithmetic in its head.** All numbers come from code, because LLMs make mistakes with mental math.
- **Every number cites its source:** the filing, the fiscal period, and the XBRL tag. Missing data is reported as missing, not estimated.
- **Data beats memory.** "Most recent" means the latest fiscal year in the SEC data, not what the model remembers from training. If a tool fails, the memo says so instead of filling in numbers from memory.
- **Guardrails:** no buy/sell recommendations, and facts are kept separate from interpretation.
- **Charts without AI:** the dashboard is computed from the fetched filing data, so it can't hallucinate.
- **Caching:** SEC responses are cached in `.cache/`, so after one rehearsal run the live demo is fast and doesn't depend on the network.
- **Traceability:** each run saves the memo and a JSON trace of every tool call in `outputs/`.

### Tech stack
Python · LLM tool/function calling (Google Gemini via its OpenAI-compatible API; the agent loop also supports Groq, OpenRouter, Ollama, and Claude) · SEC EDGAR APIs · pandas · BeautifulSoup · Streamlit · Altair

---

## Setup (about 5 minutes)

```bash
cd sec-analyst-agent
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env: add your Gemini API key + name/email
```

- **API key:** get a free Gemini key at https://aistudio.google.com/apikey and put it in `.env` as `GEMINI_API_KEY`. The app reads it from `.env` only and never displays it. After changing `.env`, restart the app.
- **SEC User-Agent:** the SEC requires a name and email with every request. Set `SEC_USER_AGENT` in `.env`. It is sent to the SEC only and isn't shown in the app.
- **Use the virtual environment.** The calculation sandbox runs Python in isolated mode, which ignores packages installed in your per-user folder. Installing into a venv avoids "No module named …" errors from `run_python`.

### Models
The web app runs on Google Gemini and offers two models in the sidebar:

| Model | Best for |
|---|---|
| `gemini-3.5-flash-lite` (default) | Fast runs and a higher free quota |
| `gemini-3.8-flash` | Stronger reasoning, but it hits free-tier rate limits sooner |

To add a model later, add it to `GEMINI_MODELS` at the top of `app.py`. Google retires older models periodically (the 2.5 series is no longer available to new users), and a retired model returns a 404.

The command-line runner and agent loop also support other providers through `LLM_PROVIDER` in `.env`:

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
3. **Fallback chain:** if a model is still throttled or its daily quota is used up, it switches to the next model, for example `gemini-3.5-flash-lite` → `gemini-3.8-flash` → Groq (if you added a Groq key), and keeps going in the same run. To stay on Gemini only, set `LLM_FALLBACKS=gemini:gemini-3.8-flash` in `.env`.
4. **Fewer tokens:** older tool results are shortened before each call, the 10-K excerpts are capped, and the prompt asks for all the math in one Python call.

If a run still fails, the app shows a plain-language message ("Daily quota reached", "Model unavailable", "Invalid API key") with the technical error in a collapsed section.

**Replay mode (demo backup):** each run is saved in `outputs/`. In the app sidebar, choose **Replay a saved run** to play a previous run back step by step with **no API calls**. Replays show the same progress box, dashboard, memo, and research steps as a live run.

## Run

**Web app:**
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
├── app.py                     # Streamlit web app (progress, banner, tabs, errors)
├── dashboard.py               # headline tiles + charts built from the SEC data
├── .streamlit/config.toml     # app theme (light and dark)
├── cli.py                     # command-line runner
├── tests/test_offline.py      # mocked-data tests for tools + agent loop
├── tests/test_ratelimit.py    # tests for retry, fallback, history trimming
├── outputs/                   # saved memos and tool traces
├── PRESENTATION.md            # 10-minute talk track + demo script
└── requirements.txt
```

## Limitations and next steps
- It uses annual 10-K data only. Next step: add 10-Q data for trailing-twelve-month figures.
- XBRL tags vary by company, so some metrics (such as debt for banks) may come back missing. The dashboard skips any chart it doesn't have data for.
- Possible extensions: PDF export of the memo, peer comparison (two tickers side by side), an earnings-call transcript tool, and a multi-agent bull/bear/PM debate.

## Links
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Gemini function calling: https://ai.google.dev/gemini-api/docs/function-calling
- Gemini OpenAI compatibility: https://ai.google.dev/gemini-api/docs/openai
- Anthropic tool use docs: https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview
- Streamlit: https://streamlit.io
