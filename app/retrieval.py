"""Vector similarity retrieval over the ingested runbook chunks."""

from __future__ import annotations

import numpy as np
from psycopg.rows import dict_row

from app.db.pg import get_pg
from app.embedding import embed_texts

# `<=>` is pgvector's cosine-distance operator. It is paired with the
# vector_cosine_ops HNSW index on chunks.embedding, so ordering by it uses the
# approximate-nearest-neighbour index. Distance runs 0 (identical direction) to
# 2 (opposite); lower means more similar.
_SEARCH_SQL = """
SELECT runbook_id,
       runbook_title,
       category,
       chunk_index,
       content,
       (embedding <=> %s::vector) AS distance
FROM chunks
ORDER BY embedding <=> %s::vector
LIMIT %s
"""


def retrieve(query: str, k: int = 6) -> list[dict]:
    """Return the k chunks most similar to the query (lower distance = closer)."""
    query_vector = np.array(embed_texts([query])[0], dtype=np.float32)
    with get_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_SEARCH_SQL, (query_vector, query_vector, k))
            return cur.fetchall()


if __name__ == "__main__":
    from pprint import pprint

    for row in retrieve("postgres replica is lagging behind the primary"):
        pprint({**row, "content": row["content"][:80] + "..."})
