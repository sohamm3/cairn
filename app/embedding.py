"""Sentence-transformers embedding model, loaded once as a module singleton."""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"  # 384-dimensional sentence embeddings

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Return the shared model, loading it on first use."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts into 384-dim vectors (cosine-normalized)."""
    model = _get_model()
    embeddings = model.encode(texts, normalize_embeddings=True)
    return [vector.tolist() for vector in embeddings]
