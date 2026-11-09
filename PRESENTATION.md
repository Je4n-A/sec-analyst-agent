# 10-Minute Presentation: Talk Track & Demo Script

## Before the call (do this tonight, and again 30 minutes before)
- [ ] `streamlit run app.py` starts with no errors. If you changed `.env` or the theme, restart it.
- [ ] **Rehearsal run:** run AAPL and T once each. This fills `.cache/` so the live SEC calls are instant, and saves replayable runs in `outputs/`.
- [ ] Check all three tabs (Dashboard, Analyst memo, Research steps) on the rehearsal run
- [ ] Test **Replay a saved run** in the sidebar once so you know where it is
- [ ] Use **Gemini 3.5 Flash-Lite** (the default) for the live demo. It has the higher free quota.
- [ ] Don't do lots of test runs right before presenting. That uses up the free per-minute and daily quota.
- [ ] Keep one finished memo open in a tab (`outputs/AAPL_..._memo.md`) as a **backup** in case the live run fails
- [ ] Teams: share the **browser window** (not the whole screen), zoom to 125%, close other tabs and notifications

---

## 1. Introduce the agent: purpose (≈2 min)
**Say:**
> "My agent is the **SEC Filing Analyst**. When an analyst picks up a new company, the first few hours go to pulling 10-Ks, building a ratio spreadsheet, and reading MD&A to understand why the numbers moved. This agent does that first pass in about a minute.
>
> You give it a ticker and a question, like 'is earnings quality improving?' It goes to the SEC, pulls the actual financial data, calculates the ratios, reads management's discussion, and writes a memo where every number cites its source. It also builds a dashboard of the key trends from the same filing data.
>
> It's built for analysis, not advice. It flags risks and open questions for a human to decide on."

**Key point:** the problem is that research is slow and LLMs make up numbers. The solution is an agent that grounds its answers in primary data.

## 2. How I built it (≈3 min)
Show `README.md` (the architecture diagram), then briefly `prompts/system_prompt.md` and `agent/tools.py`.

**Say:**
> "It's a **tool-using agent** built in Python. It runs on Google's Gemini through the free tier, but the loop is model-agnostic: one setting switches it to Groq, a local model through Ollama, or Claude. There are three parts.
>
> **First, the brain:** an LLM with a system prompt that defines its role, a 5-step workflow, the output format, and rules. The most important rules are 'every number must come from a tool' and 'no buy/sell calls.'
>
> **Second, the tools.** I wrote three:
> - `get_company_facts` calls the SEC's free XBRL API. One challenge was that companies change the accounting tags they use. Apple's revenue tag changed after ASC 606, so I merge the candidate tags and keep only 12-month 10-K periods.
> - `get_filing_section` downloads the 10-K and extracts MD&A. It skips the table-of-contents match by taking the longest section.
> - `run_python`. **The model never does math in its head.** It writes pandas code, which runs in a sandboxed process.
>
> **Third, the loop**, in `core.py`, about 60 lines. It sends the conversation to the model. If the model asks for a tool, the loop runs it and sends the result back, repeating until the memo is done. That loop is what makes it an *agent* rather than a chatbot: it decides its own next steps.
>
> On top of that is a Streamlit app. The dashboard charts are drawn straight from the SEC data, not by the AI, so they can't hallucinate.
>
> I also wrote offline tests with mocked SEC data and a scripted fake LLM, so I can check the logic without paying for API calls."

**Steps I followed:** define the use case → write the system prompt → build and test each tool on its own → write the agent loop → add the Streamlit UI and dashboard → test and refine the prompt on real companies.

**Optional, if you have time: a debugging story** (good for showing what you learned):
> "At one point the agent said Apple's latest year was FY2024, even though the FY2026 10-K was already filed. The SEC data was correct. The problem was that the calculation sandbox was silently failing, so the model gave up on its tools and answered from memory. I fixed the sandbox, and added a rule to the prompt: 'most recent' means the latest year in the data, and if a tool fails, say so instead of guessing. That's exactly the hallucination risk this project is designed around."

## 3. Live demo (≈4 min)
1. **Run AAPL with the earnings-quality question.** While it runs, point at the progress box:
   - "You can see each step as the agent decides on it: first it pulls the audited financials, then it reads MD&A, then it runs calculations."
   - When it collapses to **Analysis complete**: "About a minute, start to finish."
2. **Banner:** "It identifies the company and the exact filing it used, with a link straight to the 10-K on SEC.gov."
3. **Dashboard tab:**
   - Headline tiles: "Revenue, net income, cash flow, and margin, each compared with the prior year."
   - Cash flow vs. net income chart: "This answers the earnings-quality question visually. When operating cash flow is above net income, profits are backed by real cash."
   - Hover over a bar: "Exact values from the filing."
4. **Analyst memo tab:** walk through the bottom line, the metrics table with trend arrows, the drivers, the ranked red flags, the open questions, and the sources.
5. **Research steps tab:** "This is the audit trail. Each step shows the data it retrieved, the calculation results, and the 10-K excerpt it read."
6. **Second run, if time allows:** click the **T (AT&T): Leverage** preset. This shows it adapts its focus, here to debt and interest coverage. The debt and cash chart is the one to show.
7. **Traceability:** "Every run is also saved as a JSON trace in `outputs/`, so the analysis can be audited later."

*If the live run hits a rate limit:* a "⏳ waiting / switching to fallback" line appears in the progress box. Point it out: "The free API throttled me, so the agent backs off and retries automatically, and that's a production feature."
*If it fails completely:* the app shows a plain-language message such as "Daily quota reached". Switch the sidebar to **Replay a saved run**: "Here's the same agent's run from earlier, replayed step by step." The replay shows the same dashboard and memo.

## 4. Wrap-up (≈1 min)
> "To sum up: it's grounded in primary SEC data, the math is done in code, every number is cited, the charts come straight from the filings, and it has guardrails against giving advice.
> **Limitations:** annual data only, and XBRL tags vary for banks and insurers.
> **Next steps:** quarterly TTM data, peer comparisons, and a multi-agent version with bull, bear, and portfolio-manager agents that debate. Thank you, happy to take questions."

## Likely Q&A
- **Why not just ask ChatGPT?** It answers from memory and can make numbers up. This agent pulls the filing data live and computes everything in code, so every number can be checked. It even knows about filings newer than the model's training data, because it reads them from the SEC.
- **How do you stop hallucinations?** The rules in the system prompt, the tool-only numbers, Python for the math, and source citations. Missing data is reported, not guessed, and if a tool fails the memo says so instead of using numbers from memory.
- **Are the charts AI-generated?** No. They're drawn directly from the SEC data the agent retrieved, so they always match the filing.
- **Cost per run?** $0 on Gemini's free tier, and SEC data is free. On paid models it's roughly a few cents to about $0.25. Token usage for each run is under **Run details** in the memo tab.
- **How do you handle API limits?** It paces its calls, retries 429 errors with the delay the server suggests, falls back to another model, and trims older context to save tokens. There's also replay mode for demos.
- **Why the raw API instead of LangChain?** The raw loop is short and fully transparent, which is better for learning and debugging. Supporting two API formats also shows the agent isn't tied to one vendor. I could port it to LangGraph for multi-agent workflows.
- **Why Gemini?** It's free, handles tool calling well, and has a large context window for long 10-K sections. I use Gemini 3.5 Flash-Lite by default for its higher free quota, with Gemini 3.8 Flash as a stronger option. I also tested the loop logic for the Claude/Anthropic format.
- **What broke along the way?** Google retired the Gemini 2.5 models, so I moved to Gemini 3. Gemini 3 also attaches a hidden "thought signature" to each tool call that has to be sent back on the next turn, so I updated the loop to keep it.
