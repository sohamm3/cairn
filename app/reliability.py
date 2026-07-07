"""Reliability layer around the LLM call: timeout, retry, and circuit breaker.

Composition is ``breaker(retry(timeout(call_llm)))``:
  * timeout: each attempt is capped at 15s of wall-clock time.
  * retry:   up to 3 attempts on *transient* failures, with exponential backoff plus jitter.
  * breaker: after 5 consecutive failures the circuit opens and calls fail fast for 30s, shielding both the upstream and the caller.
"""

from __future__ import annotations

import concurrent.futures

import httpx
import pybreaker
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from app.config import settings
from app.generation import call_llm
from app.metrics import cairn_circuit_state, cairn_llm_failures_total

ATTEMPT_TIMEOUT = settings.LLM_ATTEMPT_TIMEOUT  # seconds, per attempt (configurable)

# Map the breaker's textual state to the gauge value exposed on /metrics.
_STATE_VALUE = {"closed": 0.0, "half-open": 0.5, "open": 1.0}

# Bounded pool used solely to cap each attempt's wall-clock time.
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _is_transient(exc: BaseException) -> bool:
    """Return True only for errors a later attempt might get past.

    Transient: connection/read failures, timeouts, HTTP 429 (rate limited) and
    HTTP 5xx (server-side). We deliberately do NOT retry other 4xx responses
    (400/401/403/404/422 ...): those mean the request itself is wrong (a bad
    API key, a malformed body, an unknown model), so re-sending the identical
    request would just fail the same way and waste attempts. They surface
    immediately instead.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return isinstance(exc, (httpx.TransportError, concurrent.futures.TimeoutError))


def _call_with_timeout(messages: list[dict]) -> str:
    """Run one call_llm attempt, capped at ATTEMPT_TIMEOUT seconds.

    NOTE: on timeout the underlying httpx call keeps running in its worker
    thread until the socket resolves; we stop waiting and let the retry
    proceed, rather than force-killing it. Pushing the timeout into httpx would
    cancel at the socket level.
    """
    future = _executor.submit(call_llm, messages)
    return future.result(timeout=ATTEMPT_TIMEOUT)


_retrying = Retrying(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    # Exponential backoff (grows 1s, 2s, 4s ... capped at 10s) plus random
    # jitter so retries from many callers don't align into a thundering herd.
    wait=wait_exponential(multiplier=1, max=10) + wait_random(0, 1),
    reraise=True,  # after the final attempt, raise the real error (not RetryError)
)

# Circuit-breaker states:
#   closed:    normal operation; calls pass through and failures are counted.
#   open:      reached after fail_max (5) consecutive failures; every call
#              fails fast with CircuitBreakerError for reset_timeout (30s)
#              without touching the upstream at all.
#   half-open: entered after the 30s cooldown; the next call is a trial. If
#              it succeeds the breaker closes; if it fails it re-opens.
class _GaugeListener(pybreaker.CircuitBreakerListener):
    """Publish every breaker state transition to the Prometheus gauge.

    A state-change listener (rather than syncing only at call boundaries) makes
    the transient half-open state observable on /metrics while a trial call is
    in flight, not just closed/open.
    """

    def state_change(self, cb, old_state, new_state) -> None:
        cairn_circuit_state.set(_STATE_VALUE.get(new_state.name, 0.0))


_breaker = pybreaker.CircuitBreaker(
    fail_max=5, reset_timeout=30, listeners=[_GaugeListener()]
)
cairn_circuit_state.set(_STATE_VALUE["closed"])  # initial state


def reliable_call_llm(messages: list[dict]) -> str:
    """call_llm wrapped as breaker(retry(timeout(call_llm))).

    An actual call failure (retries exhausted) bumps cairn_llm_failures_total;
    a fast-fail while the circuit is already open does not (no call was made).
    The gauge stays live via _GaugeListener on every state transition.
    """
    try:
        return _breaker.call(lambda: _retrying(_call_with_timeout, messages))
    except pybreaker.CircuitBreakerError:
        raise
    except Exception:
        cairn_llm_failures_total.inc()
        raise


def circuit_state() -> str:
    """Current breaker state as a string: 'closed', 'open', or 'half-open'."""
    return _breaker.current_state
