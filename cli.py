"""Command-line runner.

Usage:
    python cli.py AAPL
    python cli.py MSFT "Is earnings quality improving or deteriorating?"
"""
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from agent import run_agent, save_run  # noqa: E402


def show(ev):
    if ev["type"] == "tool_call":
        args = json.dumps(ev["input"])
        print(f"\n[step {ev['step']}] -> {ev['name']}({args[:300]}{'...' if len(args) > 300 else ''})")
    elif ev["type"] == "tool_result":
        print(f"[step {ev['step']}] <- {ev['name']}: {len(ev['output']):,} chars returned")
    elif ev["type"] == "notice":
        print(f"\n[rate limit] {ev['text']}")
    elif ev["type"] == "thinking_text":
        print(f"\n[agent] {ev['text']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ticker = sys.argv[1]
    focus = sys.argv[2] if len(sys.argv) > 2 else "General fundamental health check."
    question = f"Analyze {ticker.upper()}. Focus: {focus}"
    result = run_agent(question, on_event=show)
    print("\n" + "=" * 80 + "\n" + result["memo"])
    path = save_run(ticker, question, result)
    print(f"\nSaved memo to {path}  |  steps={result['steps']}  tokens={result['usage']}")
