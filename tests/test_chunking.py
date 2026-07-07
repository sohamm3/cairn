"""Tests for app.chunking.chunk_runbook."""

from app.chunking import MAX_CHARS, OVERLAP_RATIO, chunk_runbook


def _runbook(steps):
    return {
        "_id": "rb1",
        "title": "Demo Runbook",
        "category": "postgresql",
        "summary": "A short summary.",
        "tags": ["demo", "test"],
        "steps": steps,
    }


def _step(order, instruction, command="cmd", expected="ok"):
    return {
        "order": order,
        "instruction": instruction,
        "command": command,
        "expected_result": expected,
    }


def test_n_steps_produce_n_plus_one_chunks():
    steps = [_step(i, f"Do step {i}") for i in range(1, 6)]  # 5 steps
    chunks = chunk_runbook(_runbook(steps))

    # 5 step chunks + 1 summary chunk.
    assert len(chunks) == len(steps) + 1

    # Summary chunk is first (index 0); step chunks follow.
    summary, step_chunks = chunks[0], chunks[1:]
    assert summary["chunk_index"] == 0
    assert summary["content"].startswith("Demo Runbook")

    # Each step chunk's index corresponds to its step order (summary took 0).
    for order, chunk in enumerate(step_chunks, start=1):
        assert chunk["chunk_index"] == order
        assert chunk["content"].startswith(f"Step {order}:")


def test_long_step_splits_with_overlap():
    long_instruction = "A" * (MAX_CHARS * 2)  # forces the step well over MAX_CHARS
    chunks = chunk_runbook(_runbook([_step(1, long_instruction)]))

    # Summary (index 0) plus the split step chunks.
    step_chunks = chunks[1:]
    assert len(step_chunks) > 1, "an over-long step should split into sub-chunks"

    overlap = int(MAX_CHARS * OVERLAP_RATIO)
    first, second = step_chunks[0]["content"], step_chunks[1]["content"]

    # No sub-chunk exceeds the window.
    assert len(first) <= MAX_CHARS

    # Consecutive sub-chunks share ~15% overlapping text: the tail of the first
    # equals the head of the second.
    assert first[-overlap:] == second[:overlap]
    assert overlap > 0
