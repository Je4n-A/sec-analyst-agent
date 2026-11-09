"""Offline tests for rate-limit handling. Run:  python tests/test_ratelimit.py"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["LLM_MIN_INTERVAL"] = "0"

from agent.core import _compact_history, run_agent  # noqa: E402
from agent.ratelimit import Candidate, ResilientCaller, is_daily_quota, suggested_delay  # noqa: E402


class RateLimitError(Exception):
    status_code = 429


def fake_client(script):
    """script: list of exceptions or return values, consumed in order."""
    calls = []

    def create(**kw):
        calls.append(kw["model"])
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def final(text):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))])


def test_delay_parsing():
    assert suggested_delay(Exception('{"retryDelay": "17s"}')) == 17
    assert suggested_delay(Exception("Please try again in 7.5s.")) == 7.5
    assert suggested_delay(Exception("Please try again in 1m2s")) == 62
    assert suggested_delay(Exception("nope")) is None
    assert is_daily_quota(Exception("Quota exceeded for metric: GenerateRequestsPerDayPerProjectPerModel"))
    assert not is_daily_quota(Exception("GenerateRequestsPerMinutePerProjectPerModel"))


def test_retry_then_success():
    client, calls = fake_client([RateLimitError("retryDelay: 3s"), final("ok")])
    waits, notices = [], []
    caller = ResilientCaller([Candidate("gemini", "m1", client)], emit=notices.append, sleep=waits.append)
    assert caller.create(messages=[]).choices[0].message.content == "ok"
    assert waits == [4.0] and calls == ["m1", "m1"] and "Waiting 4s" in notices[0]["text"]


def test_daily_quota_falls_back_immediately():
    a, a_calls = fake_client([RateLimitError("RequestsPerDay exceeded")])
    b, b_calls = fake_client([final("from groq")])
    notices = []
    caller = ResilientCaller([Candidate("gemini", "flash", a), Candidate("groq", "gpt-oss", b)],
                             emit=notices.append, sleep=lambda s: None)
    assert caller.create(messages=[]).choices[0].message.content == "from groq"
    assert a_calls == ["flash"] and b_calls == ["gpt-oss"]
    assert "Switching" in notices[-1]["text"]


def test_all_exhausted_raises_friendly_error():
    a, _ = fake_client([RateLimitError("429")] * 10)
    caller = ResilientCaller([Candidate("gemini", "flash", a)], sleep=lambda s: None)
    try:
        caller.create(messages=[])
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "Replay mode" in str(e)


def test_non_rate_limit_errors_propagate():
    a, _ = fake_client([ValueError("bad request")])
    try:
        ResilientCaller([Candidate("gemini", "flash", a)], sleep=lambda s: None).create(messages=[])
        raise AssertionError
    except ValueError:
        pass


def test_compact_history():
    big = "x" * 5000
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": []}, {"role": "tool", "content": big},
            {"role": "assistant", "content": "", "tool_calls": []}, {"role": "tool", "content": big}]
    out = _compact_history(msgs)
    assert len(out[3]["content"]) < 1700 and "truncated" in out[3]["content"]
    assert out[5]["content"] == big  # newest result kept in full
    assert msgs[3]["content"] == big  # original not mutated


def test_agent_survives_rate_limit_mid_run():
    client, _ = fake_client([RateLimitError("retry in 2s"), final("# memo")])
    events = []
    res = run_agent("Analyze X", client=client, provider="gemini", model="m", on_event=events.append,
                    fallbacks=[])
    assert res["memo"] == "# memo"
    assert any(e["type"] == "notice" for e in events)


if __name__ == "__main__":
    import agent.ratelimit as rl
    rl.time.sleep = lambda s: None  # don't actually wait in tests
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
