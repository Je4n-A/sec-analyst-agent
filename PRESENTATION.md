# 10-Minute Presentation: Talk Track & Demo Script

## Before the call (do this tonight, and again 30 minutes before)
- [ ] `streamlit run app.py` works and the API key is loaded
- [ ] **Rehearsal run:** run AAPL and T once each. This fills `.cache/` so the live SEC calls are instant, and saves replayable runs in `outputs/`.
- [ ] Test **Replay a saved run** in the sidebar once so you know where it is
- [ ] Don't do lots of test runs right before presenting. That uses up the free per-minute and daily quota.
- [ ] Keep one finished memo open in a tab (`outputs/AAPL_..._memo.md`) as a **backup** in case the live run fails
- [ ] Teams: share the **browser window** (not the whole screen), zoom to 125%, close other tabs and notifications

---

## 1. Introduce the agent: purpose (≈2 min)
**Say:**
> "My agent is the **SEC Filing Analyst**. When an analyst picks up a new company, the first few hours go to pulling 10-Ks, building a ratio spreadsheet, and reading MD&A to understand why the numbers moved. This agent does that first pass in about a minute.
>
> You give it a ticker and a question, like 'is earnings quality improving?' It goes to the SEC, pulls the actual financial data, calculates the ratios, reads management's discussion, and writes a memo where every number cites its source.
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
> I also wrote offline tests with mocked SEC data and a scripted fake LLM, so I can check the logic without paying for API calls."

**Steps I followed:** define the use case → write the system prompt → build and test each tool on its own → write the agent loop → add the Streamlit UI → test and refine the prompt on real companies.

## 3. Live demo (≈4 min)
1. **Run AAPL with the earnings-quality question.** While it runs, open the tool-call expanders:
   - "Here it pulled 4 years of financials. Notice the XBRL tag for each number."
   - "Here's the Python it **wrote itself** to compute margins, DuPont ROE, and CFO-to-net-income."
   - "Now it's reading MD&A to explain *why*."
2. **Walk through the memo:** the bottom line, the metrics table with trend arrows, the drivers, the ranked red flags, the open questions, and the sources.
3. **Second run, if time allows:** click the **T (AT&T): Leverage** preset. This shows it adapts its focus, here to debt and interest coverage.
4. **Show traceability:** open `outputs/…_trace.json`. "Every tool call is logged, so the analysis can be audited."

*If the live run hits a rate limit:* the app shows a yellow "waiting / switching to fallback" message. Point it out: "The free API throttled me, so the agent backs off and retries automatically, and that's a production feature."
*If it fails completely:* switch the sidebar to **Replay a saved run**. "Here's the same agent's run from earlier tonight, replayed step by step."

## 4. Wrap-up (≈1 min)
> "To sum up: it's grounded in primary SEC data, the math is done in code, every number is cited, and it has guardrails against giving advice.
> **Limitations:** annual data only, and XBRL tags vary for banks and insurers.
> **Next steps:** quarterly TTM data, peer comparisons, and a multi-agent version with bull, bear, and portfolio-manager agents that debate. Thank you, happy to take questions."

## Likely Q&A
- **Why not just ask ChatGPT?** It answers from memory and can make numbers up. This agent pulls the filing data live and computes everything in code, so every number can be checked.
- **How do you stop hallucinations?** The rules in the system prompt, the tool-only numbers, Python for the math, and source citations. Missing data is reported, not guessed.
- **Cost per run?** $0 on Gemini's free tier, and SEC data is free. On paid models it's roughly a few cents to about $0.25. The token usage is shown under each memo.
- **How do you handle API limits?** It paces its calls, retries 429 errors with the delay the server suggests, falls back to other models or providers, and trims older context to save tokens. There's also replay mode for demos.
- **Why the raw API instead of LangChain?** The raw loop is short and fully transparent, which is better for learning and debugging. Supporting two API formats also shows the agent isn't tied to one vendor. I could port it to LangGraph for multi-agent workflows.
- **Why Gemini?** It's free, handles tool calling well, and has a large context window for long 10-K sections. I also tested the loop logic for the Claude/Anthropic format.
