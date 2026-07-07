"""Redis-backed caches for answers and query embeddings.

Answers are keyed by a hash of the *normalized* question (lowercased, trimmed)
so trivial variations ("Fix replica lag" / "  fix replica lag  ") share one
entry. Embeddings are keyed by a hash of the raw text (exact match matters).
Values are stored as JSON.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.db.cache import get_redis

_ANSWER_PREFIX = "cairn:answer:"
_EMBEDDING_PREFIX = "cairn:embedding:"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _answer_key(question: str) -> str:
    return _ANSWER_PREFIX + _digest(question.strip().lower())


def _embedding_key(text: str) -> str:
    return _EMBEDDING_PREFIX + _digest(text)


def answer_cache_get(question: str) -> dict | None:
    raw = get_redis().get(_answer_key(question))
    return json.loads(raw) if raw is not None else None


def answer_cache_set(question: str, response: dict, ttl: int = 3600) -> None:
    get_redis().set(_answer_key(question), json.dumps(response), ex=ttl)


def embedding_cache_get(text: str) -> list[float] | None:
    raw = get_redis().get(_embedding_key(text))
    return json.loads(raw) if raw is not None else None


def embedding_cache_set(text: str, embedding: list[float], ttl: int = 86400) -> None:
    get_redis().set(_embedding_key(text), json.dumps(embedding), ex=ttl)
