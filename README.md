# cairn

Python 3.11 service skeleton. Infrastructure and scaffolding only; no business logic yet.

## Stack

- **FastAPI** + **uvicorn**: HTTP API
- **MongoDB** (`pymongo`): document store
- **PostgreSQL** + **pgvector** (`psycopg`): relational store with vector search
- **Redis** (`redis`): cache
- **sentence-transformers**: embeddings
- **prometheus-client**: metrics
- **tenacity** / **pybreaker**: retries and circuit breaking
- **pydantic** / **pydantic-settings**: models and config

## Layout

```
app/
  main.py       # FastAPI app + GET /health
  config.py     # settings from environment
  db/
    mongo.py     # get_mongo()  -> pymongo database handle
    pg.py        # get_pg()     -> psycopg connection
    cache.py     # get_redis()  -> redis client
scripts/
  init-db.sql   # enables the pgvector extension on first boot
tests/
```

## Configuration

Copy `.env.example` to `.env` and fill in the values. Every variable read by
`app/config.py` is listed there.

```
cp .env.example .env
```

## Running with Docker

```
docker compose up --build
```

This starts MongoDB, PostgreSQL (pgvector), Redis, and the app. The API is
exposed on http://localhost:8000; check http://localhost:8000/health.

## Running locally

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Tests

```
pytest
```
