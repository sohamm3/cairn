import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.cache import answer_cache_get, answer_cache_set
from app.db.cache import get_redis
from app.db.mongo import get_mongo
from app.db.pg import get_pg
from app.generation import generate_answer
from app.metrics import (
    cairn_cache_events_total,
    cairn_llm_seconds,
    cairn_request_seconds,
    cairn_requests_total,
    cairn_retrieval_seconds,
    metrics_app,
)
from app.retrieval import retrieve

app = FastAPI(title="cairn")
app.mount("/metrics", metrics_app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class AskRequest(BaseModel):
    question: str


def _finalize(result: dict) -> dict:
    """Normalize a generation result to the {answer, sources, degraded} shape."""
    return {
        "answer": result["answer"],
        "sources": result["sources"],
        "degraded": bool(result.get("degraded", False)),
    }


@app.post("/ask")
def ask(req: AskRequest):
    start = time.perf_counter()
    question = req.question
    try:
        if not question or not question.strip():
            raise HTTPException(status_code=400, detail="question must not be empty")

        cached = answer_cache_get(question)
        if cached is not None:
            cairn_cache_events_total.labels(result="hit").inc()
            response = _finalize(cached)
            outcome = "degraded" if response["degraded"] else "success"
            cairn_requests_total.labels(outcome=outcome).inc()
            cairn_request_seconds.observe(time.perf_counter() - start)
            return response

        cairn_cache_events_total.labels(result="miss").inc()

        with cairn_retrieval_seconds.time():
            chunks = retrieve(question)
        with cairn_llm_seconds.time():
            result = generate_answer(question, chunks)

        # Only cache successful answers. Caching a degraded ("temporarily
        # unavailable") response would poison the cache for the whole TTL,
        # serving the stale failure even after the LLM recovers.
        if not result.get("degraded"):
            answer_cache_set(question, result, ttl=3600)

        response = _finalize(result)
        outcome = "degraded" if response["degraded"] else "success"
        cairn_requests_total.labels(outcome=outcome).inc()
        cairn_request_seconds.observe(time.perf_counter() - start)
        return response

    except HTTPException:
        # Deliberate rejections (empty question) propagate as their status code.
        raise
    except Exception:
        # Any unhandled failure still returns a JSON body, never a raw 500 leak.
        cairn_requests_total.labels(outcome="error").inc()
        cairn_request_seconds.observe(time.perf_counter() - start)
        return JSONResponse(
            status_code=200,
            content={
                "answer": "The service hit an unexpected error and could not answer.",
                "sources": [],
                "degraded": True,
            },
        )


@app.get("/health/ready")
def health_ready():
    """Readiness: all three datastores must respond, else 503 naming the failure."""
    try:
        get_mongo().command("ping")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "error": "mongo"})

    try:
        with get_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "error": "postgres"})

    try:
        get_redis().ping()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "error": "redis"})

    return {"status": "ready"}
