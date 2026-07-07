"""API tests for /ask and /health/ready using FastAPI's TestClient."""

import uuid

from fastapi.testclient import TestClient

import app.reliability as reliability
from app.main import app

client = TestClient(app)


def test_ask_returns_answer_and_sources(monkeypatch):
    reliability._breaker.close()
    # Deterministic, canned LLM response; no real endpoint is contacted.
    monkeypatch.setattr(
        reliability, "call_llm", lambda messages: "Canned deterministic answer."
    )

    # Unique question avoids hitting a previously cached answer.
    question = f"How do I recover a lagging postgres replica? {uuid.uuid4()}"
    resp = client.post("/ask", json={"question": question})

    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert "sources" in body
    assert isinstance(body["sources"], list)
    assert len(body["sources"]) > 0


def test_health_ready_returns_200_or_503():
    resp = client.get("/health/ready")
    assert resp.status_code in (200, 503)
    body = resp.json()
    if resp.status_code == 200:
        assert body["status"] == "ready"
    else:
        assert body["status"] == "not_ready"
        assert "error" in body
