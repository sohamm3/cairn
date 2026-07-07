"""LLM answer generation grounded in retrieved runbook chunks.

Targets an OpenAI-compatible chat-completions endpoint (OpenAI, a local Ollama
server, etc.) configured via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL.
"""

from __future__ import annotations

import httpx

from app.config import settings

SYSTEM_PROMPT = (
    "You are Cairn, a DBA runbook assistant. Answer ONLY from the provided "
    "runbook excerpts. For every claim, cite the runbook title and the step "
    "number it comes from, e.g. \"[Recover a PostgreSQL replica from WAL lag, "
    "Step 3]\". If the excerpts do not answer the question, reply exactly with "
    "\"I don't have a runbook covering that\" and do not invent steps."
)


def call_llm(messages: list[dict]) -> str:
    """Make a single OpenAI-compatible chat-completions request.

    One call, raise on HTTP error. The socket timeout is the per-attempt budget
    (LLM_ATTEMPT_TIMEOUT); without an explicit value httpx would apply its 5s
    default and cut off any slower model. Retries/circuit-breaking live in
    app.reliability, not here.
    """
    payload = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    if settings.LLM_MAX_TOKENS is not None:
        payload["max_tokens"] = settings.LLM_MAX_TOKENS
    response = httpx.post(
        f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
        json=payload,
        timeout=settings.LLM_ATTEMPT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _format_chunks(chunks: list[dict]) -> str:
    """Render retrieved chunks as a numbered list for the user message."""
    return "\n\n".join(
        f"[{i}] {chunk['runbook_title']}\n{chunk['content']}"
        for i, chunk in enumerate(chunks, start=1)
    )


def generate_answer(question: str, chunks: list[dict]) -> dict:
    """Answer a question from retrieved chunks, returning the answer + sources.

    The LLM call goes through the reliability layer (timeout + retry + circuit
    breaker). If it ultimately fails (the circuit is open or retries are
    exhausted), we degrade gracefully rather than raising: the caller still
    gets the retrieved excerpts, flagged ``degraded: True``.
    """
    # Local import breaks the app.generation <-> app.reliability import cycle
    # (reliability imports call_llm from this module).
    from app.reliability import reliable_call_llm

    user_message = (
        "Runbook excerpts:\n\n"
        f"{_format_chunks(chunks)}\n\n"
        f"Question: {question}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    sources = [
        {
            "runbook_id": chunk["runbook_id"],
            "runbook_title": chunk["runbook_title"],
            "chunk_index": chunk["chunk_index"],
        }
        for chunk in chunks
    ]

    try:
        answer = reliable_call_llm(messages)
    except Exception:
        # Never raise to the caller. Return the retrieved excerpts (with their
        # contents) so the operator still has something actionable while
        # generation is unavailable.
        return {
            "answer": (
                "Generation is temporarily unavailable. "
                "Here are the most relevant runbook excerpts:"
            ),
            "sources": [
                {**source, "content": chunk["content"]}
                for source, chunk in zip(sources, chunks)
            ],
            "degraded": True,
        }

    return {"answer": answer, "sources": sources}
