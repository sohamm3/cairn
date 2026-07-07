# Cairn

A retrieval-augmented generation (RAG) service that answers on-call database questions from a runbook library, citing the exact runbook and step behind every claim, and still returns useful excerpts even when the LLM itself is unreachable.

## Overview

When a database incident hits, the answer usually already exists in a runbook somewhere, but finding the right one under pressure is slow, and a general-purpose LLM asked the same question will confidently invent steps it has no business inventing. Cairn constrains generation to a fixed corpus of DBA runbooks: it retrieves the most relevant excerpts with vector search, hands only those excerpts to the model, and requires every claim in the answer to cite a runbook title and step number. If the excerpts don't cover the question, the model is instructed to say so rather than improvise.

The corpus shipped with this repo (20 runbooks across PostgreSQL, MongoDB, MySQL, and Redis) is synthetic demo data, not real production runbooks. It exists to exercise the pipeline end to end.

Equally important: an LLM outage should not mean the service goes down. Retrieval and generation are decoupled enough that a dead model degrades the response, it doesn't break it.

## Request lifecycle

`POST /ask` runs a four-stage pipeline:

1. **Cache lookup.** The question is normalized (lowercased, trimmed) and hashed (SHA-256); a hit returns immediately.
2. **Retrieve.** On a miss, the question is embedded with `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dimensions, cosine-normalized) and matched against a `pgvector` HNSW index using cosine distance (`<=>`). The 6 nearest chunks come back.
3. **Generate.** The retrieved chunks and the question are sent to an OpenAI-compatible chat-completions endpoint through a reliability layer: timeout, then retry, then circuit breaker.
4. **Degrade if needed.** If generation ultimately fails, the response falls back to the retrieved excerpts themselves with `degraded: true`, instead of raising an error. Degraded responses are never cached, since caching one would keep serving a stale "unavailable" message for the full TTL even after the model recovers. Successful answers are cached for 1 hour.

### Data stores

Each store does the one thing it's actually good at:

- **MongoDB** is the source of truth for runbooks, users, and execution history. Runbooks embed their steps and tags (read together, bounded size); execution history is a separate, unbounded collection referenced by id, since embedding an ever-growing log would eventually hit MongoDB's 16 MB document cap.
- **PostgreSQL + pgvector** holds the chunked, embedded runbook text for similarity search. Postgres has no native document flexibility, but it has mature ANN indexing, which Mongo doesn't.
- **Redis** caches full answers (1 hour TTL) and is the client for future embedding caching (see [Known limitations](#known-limitations)).

### Module map

| File | Responsibility |
|---|---|
| `app/main.py` | FastAPI app: `/health`, `/health/ready`, `POST /ask`, and the `/metrics` mount |
| `app/retrieval.py` | Embeds the query and runs the pgvector cosine kNN search (`retrieve(query, k=6)`) |
| `app/embedding.py` | Lazy-singleton `SentenceTransformer` model, `embed_texts()` |
| `app/generation.py` | Builds the prompt, calls the LLM, and implements graceful degradation |
| `app/reliability.py` | Timeout (thread pool) + `tenacity` retry + `pybreaker` circuit breaker around the LLM call |
| `app/cache.py` | Redis-backed answer cache (and an embedding-cache API, currently unused) |
| `app/chunking.py` | Splits a runbook document into retrievable chunks |
| `app/metrics.py` | Prometheus counters, histograms, and the circuit-state gauge |
| `app/queries.py` | A read-side Mongo aggregation example (`$lookup` + `$group` over runbooks) |
| `app/models.py` | Pydantic models mirroring the Mongo schema |
| `app/db/` | Thin connection getters: `get_mongo()`, `get_pg()`, `get_redis()` |
| `scripts/seed_mongo.py` | Idempotently seeds 5 users, 20 runbooks, and 30 execution-log entries |
| `scripts/ingest.py` | Chunks and embeds every runbook from Mongo into the `chunks` table in Postgres |
| `scripts/create_indexes.py` | Builds Mongo's compound index and verifies it's actually used by the query planner |
| `scripts/verify_fallback.py` | Standalone script proving the degrade path works against a dead LLM endpoint |

## Prerequisites

- Docker and Docker Compose v2 (the `docker compose` subcommand)
- An OpenAI-compatible LLM endpoint, one of:
  - An OpenAI API key, or
  - A locally running [Ollama](https://ollama.com) server (or any other OpenAI-compatible server)

Nothing else needs to be installed on the host. The app's Python dependencies only exist inside the container image; there's no requirement to have Python, MongoDB, PostgreSQL, or Redis installed locally.

## Installation

```bash
git clone <this-repo>
cd cairn
cp .env.example .env
```

Edit `.env` and fill in the LLM section (see [Configuration](#configuration) and [Local LLM](#local-llm-ollama) below). The database URLs in `.env.example` already point at the Compose service hostnames and don't need to change for a Docker-based run.

## Configuration

All configuration is environment variables, read once at startup by `app/config.py` (via `pydantic-settings`). Every variable below is required unless a default is listed; if a required variable is missing, the app fails immediately at import time rather than at request time.

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_URI` | *(required)* | Mongo connection string. Must include the database name in the path (e.g. `mongodb://mongo:27017/cairn`); `get_mongo()` relies on `get_default_database()`, so a URI without a path segment breaks it. |
| `PG_DSN` | *(required)* | PostgreSQL DSN for the `chunks` table (e.g. `postgresql://postgres:cairn@postgres:5432/postgres`). |
| `REDIS_URL` | *(required)* | Redis connection URL for the answer/embedding caches. |
| `LLM_API_KEY` | *(required)* | Bearer token sent as `Authorization: Bearer <key>`. Some local servers (Ollama included) ignore its value but still expect the header, so any placeholder string works. |
| `LLM_BASE_URL` | *(required)* | Base URL of an OpenAI-compatible chat-completions API, with `/chat/completions` appended automatically. |
| `LLM_MODEL` | *(required)* | Model name sent in the request body. |
| `LLM_ATTEMPT_TIMEOUT` | `15` | Per-attempt timeout in seconds, used both as the httpx socket timeout and the thread-pool wait budget. Raise this for slow local models; the default is a strict production-style SLA. |
| `LLM_MAX_TOKENS` | unset (provider default) | Caps generated tokens. Useful for bounding latency on slow models; unset means no limit is sent. |

## Running the project

### With Docker Compose (recommended path)

```bash
# Build and start Mongo, Postgres (pgvector), Redis, and the app
docker compose up -d --build

# Wait for the app to come up, then confirm
curl -s http://localhost:8000/health
# -> {"status":"ok"}
```

Then seed the data, in order (Mongo first, since ingestion reads from it):

```bash
# 1. Seed MongoDB with users, runbooks, and execution history
docker compose exec -T app python -m scripts.seed_mongo

# 2. Chunk and embed every runbook into pgvector
docker compose exec -T app python -m scripts.ingest

# 3. (Optional) Build Mongo's compound index and confirm the query planner uses it
docker compose exec -T app python -m scripts.create_indexes
```

Both seed scripts are idempotent: re-running them converges to the same state rather than creating duplicates.

> **Seeding idempotency:** every document's `_id` is a deterministic `ObjectId` derived from a natural key (user email, runbook slug, or execution index) via an MD5 digest. `replace_one(..., upsert=True)` overwrites in place on every run, and cross-references resolve to the same ids every time. `scripts/ingest.py` is idempotent differently: it truncates and reloads the `chunks` table wholesale on every run.

The app image bakes the source into the container at build time (`COPY . .` in the `Dockerfile`), so after editing anything under `app/` or `scripts/`, rebuild before testing:

```bash
docker compose up -d --build app
```

To stop the stack: `docker compose down` (keeps data volumes) or `docker compose down -v` (wipes them, next `up` starts from empty databases).

### Running locally without Docker

Use this only if you need to run the app process outside a container (debugging, profiling, etc.) while the databases still run in Compose. The `.env.example` values point at Compose's internal hostnames (`mongo`, `postgres`, `redis`), which don't resolve from the host, so override them:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

MONGO_URI=mongodb://localhost:27017/cairn \
PG_DSN=postgresql://postgres:cairn@localhost:5432/postgres \
REDIS_URL=redis://localhost:6379/0 \
uvicorn app.main:app --reload
```

The `mongo`, `postgres`, and `redis` containers still need to be up (`docker compose up -d mongo postgres redis`) for this to work.

## Using the API

```bash
# Liveness: process is up
curl -s http://localhost:8000/health

# Readiness: all three datastores must respond, or a 503 names which one failed
curl -s http://localhost:8000/health/ready

# Ask a question
curl -s -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "How do I recover a PostgreSQL replica that has fallen behind on WAL?"}'
```

A successful `/ask` response looks like:

```json
{
  "answer": "... cited answer text ...",
  "sources": [
    {"runbook_id": "...", "runbook_title": "Recover a PostgreSQL replica from WAL lag", "chunk_index": 3}
  ],
  "degraded": false
}
```

`sources` always reflects the full set of retrieved chunks (`k=6`), not just the ones the model actually cited in its answer; there is no code-level cross-check between the citation and the source list. When `degraded` is `true`, each source additionally includes its raw `content`, since there is no generated answer to read instead.

Prometheus metrics are served at `GET /metrics` (see [Observability](#observability-and-measured-performance)).

## Prompt & generation

The system prompt sent on every request is fixed:

> You are Cairn, a DBA runbook assistant. Answer ONLY from the provided runbook excerpts. For every claim, cite the runbook title and the step number it comes from, e.g. "[Recover a PostgreSQL replica from WAL lag, Step 3]". If the excerpts do not answer the question, reply exactly with "I don't have a runbook covering that" and do not invent steps.

The user message is built from the retrieved chunks plus the question:

```
Runbook excerpts:

[1] <runbook title>
<chunk content>

[2] <runbook title>
<chunk content>
...

Question: <the user's question>
```

Generation uses `temperature=0.1`: runbook answers need to be a faithful restatement of retrieved steps, not creative variation, so temperature is near zero (not exactly zero, to avoid degenerate repetition loops). `LLM_MAX_TOKENS`, if set, is passed through as `max_tokens`.

The refusal behavior ("I don't have a runbook covering that") is enforced entirely by the prompt. There is no code-level check that the model actually follows it, and it has only been exercised against a canned test stub, not a real model.

## Reliability

Every LLM call goes through `breaker(retry(timeout(call_llm)))`:

- **Timeout**: each attempt is capped at `LLM_ATTEMPT_TIMEOUT` seconds (default 15) using a bounded thread pool. httpx's own socket timeout is set to the same value, since httpx defaults to a 5-second read timeout that would otherwise silently override the intended budget.
- **Retry**: up to 3 attempts total, but only for transient failures: connection/read errors, timeouts, HTTP 429, and HTTP 5xx. Any other 4xx (bad API key, malformed body, unknown model) fails immediately, since retrying it would just repeat the same failure. Backoff grows exponentially (roughly 1s, 2s, 4s, capped at 10s) plus 0-1s of random jitter, so many clients failing at once don't retry in lockstep.
- **Circuit breaker**: after 5 consecutive failures, the circuit opens and every call fails fast for 30 seconds without touching the upstream at all. After that cooldown, a single half-open trial call decides whether to close the circuit or reopen it. State transitions are published live to the `cairn_circuit_state` gauge.

If the call ultimately fails (retries exhausted, or the circuit is already open), `generate_answer()` catches the failure and returns the retrieved excerpts with `degraded: true` instead of propagating an error. `POST /ask` itself has a top-level exception handler that always returns a 200 with a JSON body, `degraded: true`, even for failures the reliability layer didn't anticipate; it never lets a raw 500 through.

You can prove this path works end to end with:

```bash
docker compose exec -T app python scripts/verify_fallback.py
```

This points the LLM at a closed port before importing anything, then confirms retrieval still succeeds, generation degrades, and the response is flagged accordingly.

## Observability and measured performance

### Metrics

`GET /metrics` exposes standard `prometheus_client` output plus:

| Metric | Type | What it tells you |
|---|---|---|
| `cairn_requests_total{outcome}` | Counter | `/ask` requests by `success` / `degraded` / `error` |
| `cairn_llm_failures_total` | Counter | LLM calls where retries were exhausted (a fast-fail while the circuit is already open doesn't count, since no call was made) |
| `cairn_cache_events_total{result}` | Counter | Answer-cache lookups by `hit` / `miss` |
| `cairn_request_seconds` | Histogram | End-to-end `/ask` latency |
| `cairn_retrieval_seconds` | Histogram | Embed + vector-search latency |
| `cairn_llm_seconds` | Histogram | LLM generation latency |
| `cairn_circuit_state` | Gauge | `0` closed, `0.5` half-open, `1` open |

### Measured performance

These numbers come from running the seeded 20-runbook corpus against a local CPU-bound `tinyllama` model via Ollama, timed from inside the app container so no external network sits in the path. They describe *this* setup's behavior, not a hosted-LLM production deployment; where that matters, it's called out below.

**Cache speedup.** Five distinct questions, each measured cold (cache cleared, 3 repetitions averaged) and warm (cache hit, 3 repetitions averaged), with a separate warmup call absorbing one-time model-loading cost so these are steady-state numbers, not first-request numbers:

| Question | Cold avg (3 reps) | Warm avg (3 reps) | Per-question multiplier |
|---|---|---|---|
| Recover PG replica lagging on WAL | 30.24 s | 0.1595 s | 189.6x |
| Resync stale MongoDB secondary | 25.97 s | 0.0238 s | 1089.4x |
| Recover MySQL GTID replication | 23.91 s | 0.0074 s | 3244.7x |
| Diagnose Redis latency spikes | 24.85 s | 0.5105 s | 48.7x |
| Online MySQL schema change (gh-ost) | 25.63 s | 0.0056 s | 4542.8x |
| **Pooled (cold avg / warm avg)** | **26.12 s** | **0.1414 s** | **~185x** |

The per-question multiplier column is included for transparency but is not a reliable summary: it swings from 48.7x to 4542.8x purely because it divides by very small warm-latency numbers, two of which (0.35s and 1.47s, out of fifteen warm reps) were themselves outliers with an unconfirmed cause, likely host scheduling jitter. The defensible aggregate is the pooled figure: **26.12s average cold vs. 141.4ms average warm, roughly a 185x speedup**, with a warm median of 8.5ms. Because the underlying model here is a slow CPU-bound LLM, this ratio is inflated by how slow the "cold" side is; against a hosted production LLM (typically 2-5s per generation), the same cache would produce something closer to a 15-50x speedup. The transferable, model-independent fact is the cache hit latency itself: millisecond-class, median 8.5ms.

**Retrieval latency distribution.** 20 distinct queries (one per seeded runbook) x 5 repetitions each = 100 timed calls to `retrieve()`, the real embed-and-search path used by `/ask`:

| Percentile / stat | Value |
|---|---|
| p50 (median) | 36.4 ms |
| p95 | 59.4 ms |
| p99 | 82.8 ms |
| mean | 40.4 ms |
| min | 26.4 ms |
| max | 193.0 ms (single outlier) |

p50 is the latency half of all queries beat; p95 and p99 describe the slow tail, i.e. the experience of the 1-in-20 and 1-in-100 worst queries, which matters more than the mean when you care about worst-case responsiveness. At this corpus size (169 chunks), retrieval is consistently sub-100ms outside of one outlier rep.

No answer-quality evaluation (a gold-set of question/expected-answer pairs, graded for correctness) exists in this repo. The measurements above are performance/latency characteristics only, not a measure of whether the generated answers are actually correct.

## Local LLM (Ollama)

`docker-compose.yml` sets `extra_hosts: host.docker.internal:host-gateway` on the app service, so a model server running on the Docker host (e.g. Ollama listening on `11434`) is reachable from inside the container via `host.docker.internal`, regardless of platform. Point `.env` at it:

```
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=tinyllama
```

Ollama by default only binds to `127.0.0.1` on the host, which `host.docker.internal` cannot reach on every platform/networking setup. If `/ask` always comes back `degraded: true`, this is the first thing to check: confirm the model server is actually reachable from inside the container (`docker compose exec app curl <LLM_BASE_URL>`), not just from the host. Slow local models will also want a higher `LLM_ATTEMPT_TIMEOUT` (and optionally `LLM_MAX_TOKENS` to bound generation time); 15 seconds is tuned for a responsive hosted API, not a CPU-bound local model.

## Troubleshooting

- **`/ask` always returns `degraded: true`.** The LLM endpoint is unreachable, misconfigured, or the circuit breaker is open. Check `LLM_BASE_URL` is reachable from inside the app container, check `cairn_circuit_state` on `/metrics` (`1` means open), and check `cairn_llm_failures_total` for a rising count.
- **Code changes don't seem to take effect.** The app image bakes source via `COPY . .`; rebuild with `docker compose up -d --build app` after any change under `app/` or `scripts/`.
- **App fails immediately on startup with a settings/validation error.** `app/config.py` instantiates `Settings()` at import time, so every required environment variable must be present before the app (or any script importing `app.*`) can even start. Confirm `.env` is complete and, if running outside Compose, that it's actually being loaded.
- **`/health/ready` returns 503.** The body's `error` field names which datastore failed (`mongo`, `postgres`, or `redis`); check that service's container is up.
- **Ingest reports 0 chunks, or retrieval returns nothing.** Run `scripts.seed_mongo` before `scripts.ingest`; ingestion reads runbooks from Mongo and will happily process zero of them if Mongo is empty.

## Known limitations

- **`sources` isn't the same as "cited."** It's the full retrieval set (`k=6`), not the subset the model actually referenced in its answer.
- **No connection pooling.** `get_mongo()`, `get_pg()`, and `get_redis()` each open a new connection per call. Fine at this scale; would need to become pooled or app-lifespan-managed connections under real load.
- **No request coalescing.** Concurrent identical cache-miss requests each trigger their own independent LLM call; there's no per-key lock to deduplicate them.
- **Circuit breaker and in-process metrics don't share state across workers.** Both are per-process, in-memory. Running multiple uvicorn workers or replicas means each has its own breaker and its own counters; they don't aggregate into one global view.
- **The embedding cache is unused.** `app/cache.py` defines `embedding_cache_get`/`embedding_cache_set`, but `embed_texts()` never calls them; every retrieval re-embeds the query.
- **No answer-quality evaluation.** There's no gold-set of graded question/answer pairs; only latency and degradation behavior are measured (see above).
- **The refusal instruction is prompt-only.** Nothing in code verifies the model actually says "I don't have a runbook covering that" when it should.

## Extending Cairn

- **Add a runbook:** add an entry to the `RUNBOOKS` list in `scripts/seed_mongo.py`, then re-run `python -m scripts.seed_mongo` followed by `python -m scripts.ingest` (ingestion re-chunks and re-embeds the entire corpus from Mongo, so it always reflects what's currently seeded).
- **Change chunking behavior:** `app/chunking.py` controls chunk size (`MAX_CHARS = 1200`) and overlap (`OVERLAP_RATIO = 0.15`) for splitting any single step whose rendered text is unusually long.
- **Tune reliability behavior:** retry count, backoff, timeout, and breaker thresholds are all defined in `app/reliability.py`.
- **Run the test suite:**
  ```bash
  docker compose exec -T app pytest -v
  ```
  or use `scripts/run_tests.sh`, which also prints the current `cairn_*` metrics from `/metrics` afterward for a quick sanity check.

## License

MIT. See [LICENSE](LICENSE).
