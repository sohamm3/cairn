"""Tests for the reliability layer and graceful degradation."""

import httpx
import pytest
from tenacity import wait_none

import app.reliability as reliability
from app.generation import generate_answer

CHUNKS = [
    {
        "runbook_id": "rb1",
        "runbook_title": "Demo Runbook",
        "category": "postgresql",
        "chunk_index": 0,
        "content": "excerpt body",
    }
]


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://x/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


@pytest.fixture(autouse=True)
def _reset_breaker_and_speed_up_retries(monkeypatch):
    # Fresh, closed breaker per test; zero wait so retries don't sleep.
    reliability._breaker.close()
    monkeypatch.setattr(reliability._retrying, "wait", wait_none())
    yield
    reliability._breaker.close()


def test_generate_answer_degrades_when_llm_always_fails(monkeypatch):
    # Transient error -> retried, ultimately exhausted.
    monkeypatch.setattr(
        reliability, "call_llm", lambda messages: (_ for _ in ()).throw(_http_error(503))
    )

    result = generate_answer("How do I fix replica lag?", CHUNKS)

    assert result["degraded"] is True
    assert isinstance(result["sources"], list)
    assert len(result["sources"]) > 0  # excerpts still returned


def test_4xx_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def failing(messages):
        calls["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr(reliability, "call_llm", failing)

    with pytest.raises(httpx.HTTPStatusError):
        reliability.reliable_call_llm([{"role": "user", "content": "x"}])

    assert calls["n"] == 1  # invoked exactly once, no retries on a 4xx
