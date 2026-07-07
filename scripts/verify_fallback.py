"""Standalone end-to-end proof that Cairn degrades gracefully when the LLM is down.

We point the LLM at a closed port *before importing any app module*, then run
the real retrieval + generation path. Retrieval must still succeed (pgvector is
up); generation must fail over into a degraded response instead of raising.
Exits non-zero if any check fails.

Run inside the app container (needs pgvector + the embedding model):
    docker compose exec app python scripts/verify_fallback.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# MUST happen before importing app modules. app.config instantiates Settings()
# at import time, and in pydantic-settings an OS environment variable takes
# precedence over the .env file, so setting this now forces the dead endpoint.
# A closed port yields an immediate connection error (transient) on every
# attempt, so the retry budget is exhausted and generation degrades.
DEAD_ENDPOINT = "http://localhost:9999/v1"
os.environ["LLM_BASE_URL"] = DEAD_ENDPOINT

# Allow running as a bare file path too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.generation import generate_answer  # noqa: E402
from app.retrieval import retrieve  # noqa: E402

QUESTION = "How do I recover a PostgreSQL replica that has fallen behind on WAL?"


def check(label: str, passed: bool, detail: str = "") -> bool:
    """Print a PASS/FAIL line for one assertion and return whether it passed."""
    mark = "PASS" if passed else "FAIL"
    line = f"[{mark}] {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    return passed


def main() -> int:
    print(f"LLM_BASE_URL in effect: {settings.LLM_BASE_URL}")
    print(f"Question: {QUESTION}\n")

    results: list[bool] = []

    # Guard: make sure the override actually took effect. Without this, a run
    # that silently kept the real endpoint (also unreachable from here) would
    # degrade too and pass for the wrong reason.
    results.append(
        check(
            "LLM points at the forced dead endpoint",
            settings.LLM_BASE_URL == DEAD_ENDPOINT,
            settings.LLM_BASE_URL,
        )
    )

    # Retrieval runs against the live pgvector store, unaffected by the dead LLM.
    chunks = retrieve(QUESTION)
    print(f"\nretrieved {len(chunks)} chunk(s)")
    if chunks:
        print(f"top match: {chunks[0]['runbook_title']} (distance {chunks[0]['distance']:.3f})")

    # Generation hits the dead endpoint: retries exhaust, then it degrades.
    result = generate_answer(QUESTION, chunks)
    answer = result.get("answer", "")
    sources = result.get("sources", [])

    print("\n--- generate_answer result ---")
    print(f"degraded: {result.get('degraded')!r}")
    print(f"answer:   {answer!r}")
    print(f"sources:  {len(sources)} item(s)\n")

    results.append(
        check(
            "result is flagged degraded=True",
            result.get("degraded") is True,
            f"degraded={result.get('degraded')!r}",
        )
    )
    results.append(
        check(
            "answer signals unavailability, not a normal generated answer",
            "unavailable" in answer.lower(),
            answer,
        )
    )
    results.append(
        check(
            "sources is non-empty (retrieval succeeded despite generation failing)",
            len(sources) > 0,
            f"{len(sources)} source(s)",
        )
    )

    print()
    if all(results):
        print("RESULT: PASS. Reliability layer degraded gracefully end-to-end.")
        return 0
    print("RESULT: FAIL. One or more assertions did not hold.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
