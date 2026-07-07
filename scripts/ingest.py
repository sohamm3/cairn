"""Ingest Cairn's runbooks into pgvector.

Reads every runbook from MongoDB, chunks it, embeds the chunks with
sentence-transformers, and loads them into the ``chunks`` table. Idempotent:
the table is truncated first, so re-running replaces the corpus wholesale.

Run:  python -m scripts.ingest
  (or: python scripts/ingest.py)
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Allow running as a plain file as well as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.chunking import chunk_runbook  # noqa: E402
from app.db.mongo import get_mongo  # noqa: E402
from app.db.pg import ensure_schema, get_pg  # noqa: E402
from app.embedding import embed_texts  # noqa: E402


def main() -> None:
    ensure_schema()

    runbooks = list(get_mongo()["runbooks"].find())

    chunks: list[dict] = []
    for runbook in runbooks:
        chunks.extend(chunk_runbook(runbook))

    embeddings = embed_texts([c["content"] for c in chunks])

    with get_pg() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE chunks RESTART IDENTITY;")
            cur.executemany(
                """
                INSERT INTO chunks
                    (runbook_id, runbook_title, category, chunk_index, content, embedding)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        c["runbook_id"],
                        c["runbook_title"],
                        c["category"],
                        c["chunk_index"],
                        c["content"],
                        np.array(embedding, dtype=np.float32),
                    )
                    for c, embedding in zip(chunks, embeddings)
                ],
            )
        conn.commit()

    counts = Counter(c["category"] for c in chunks)
    print(f"ingested {len(chunks)} chunks from {len(runbooks)} runbooks")
    for category in sorted(counts):
        print(f"  {category}: {counts[category]}")


if __name__ == "__main__":
    main()
