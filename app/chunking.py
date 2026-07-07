"""Turn a runbook document into retrievable text chunks.

One chunk per step, plus a summary chunk (title + summary + tags). Any step
whose rendered text is very long is split into overlapping sub-chunks so no
single chunk overflows the embedding model's useful context.
"""

from __future__ import annotations

MAX_CHARS = 1200
OVERLAP_RATIO = 0.15


def _split_long(text: str) -> list[str]:
    """Split text over MAX_CHARS into ~15%-overlapping windows."""
    if len(text) <= MAX_CHARS:
        return [text]
    overlap = int(MAX_CHARS * OVERLAP_RATIO)
    step = MAX_CHARS - overlap
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = start + MAX_CHARS
        parts.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return parts


def chunk_runbook(runbook_doc: dict) -> list[dict]:
    """Return the ordered chunks for one runbook document."""
    runbook_id = str(runbook_doc["_id"])
    title = runbook_doc["title"]
    category = runbook_doc["category"]

    contents: list[str] = []

    # Summary chunk: title + summary + tags.
    tags = ", ".join(runbook_doc.get("tags", []))
    contents.append(f"{title}\n{runbook_doc['summary']}\nTags: {tags}")

    # One chunk per step, splitting any that are too long.
    for step in runbook_doc.get("steps", []):
        text = (
            f"Step {step['order']}: {step['instruction']}\n"
            f"{step.get('command') or ''}\n"
            f"Expected: {step.get('expected_result') or ''}"
        )
        contents.extend(_split_long(text))

    return [
        {
            "runbook_id": runbook_id,
            "runbook_title": title,
            "category": category,
            "chunk_index": i,
            "content": content,
        }
        for i, content in enumerate(contents)
    ]
