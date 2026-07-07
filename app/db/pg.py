import psycopg
from pgvector.psycopg import register_vector

from app.config import settings


def get_pg() -> psycopg.Connection:
    """Open a psycopg connection with the pgvector type adapter registered.

    Registering the adapter lets numpy arrays / lists round-trip to the
    ``vector`` column type. It requires the ``vector`` extension to already
    exist in the database (the compose init script enables it on first boot,
    and ``ensure_schema`` re-asserts it).
    """
    conn = psycopg.connect(settings.PG_DSN)
    register_vector(conn)
    return conn


def ensure_schema() -> None:
    """Create the chunks table and its HNSW index if they do not exist."""
    with get_pg() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id serial PRIMARY KEY,
                    runbook_id text,
                    runbook_title text,
                    category text,
                    chunk_index int,
                    content text,
                    embedding vector(384)
                );
                """
            )
            # HNSW index for approximate nearest-neighbour search under cosine
            # distance, matching the normalized embeddings we store.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
                ON chunks USING hnsw (embedding vector_cosine_ops);
                """
            )
        conn.commit()
