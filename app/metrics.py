"""Prometheus metrics for Cairn, plus the /metrics ASGI app.

Bucket boundaries are chosen for the expected latency of each stage:
  * retrieval:  embed the query + one pgvector kNN scan; tens of ms to ~1s.
  * llm:        a chat completion (local Ollama or hosted); hundreds of ms to tens of seconds.
  * end-to-end: cache lookup + retrieval + llm; roughly the sum of the above.
"""

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

cairn_requests_total = Counter(
    "cairn_requests_total",
    "Total /ask requests by outcome.",
    ["outcome"],  # success | degraded | error
)

cairn_llm_failures_total = Counter(
    "cairn_llm_failures_total",
    "Total LLM call failures (retries exhausted).",
)

cairn_cache_events_total = Counter(
    "cairn_cache_events_total",
    "Answer-cache lookups by result.",
    ["result"],  # hit | miss
)

cairn_request_seconds = Histogram(
    "cairn_request_seconds",
    "End-to-end /ask latency in seconds.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60),
)

cairn_retrieval_seconds = Histogram(
    "cairn_retrieval_seconds",
    "Retrieval (embed + vector search) latency in seconds.",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)

cairn_llm_seconds = Histogram(
    "cairn_llm_seconds",
    "LLM generation latency in seconds.",
    buckets=(0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60),
)

cairn_circuit_state = Gauge(
    "cairn_circuit_state",
    "Circuit-breaker state: 0=closed, 0.5=half-open, 1=open.",
)

# Standard prometheus_client ASGI app, mounted at /metrics by app.main.
metrics_app = make_asgi_app()
