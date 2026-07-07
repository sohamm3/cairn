"""Create Cairn's MongoDB indexes and confirm the compound index is used.

Idempotent: ``create_index`` is a no-op when an identical index already exists,
so this can be run repeatedly. After building the indexes it runs an
``explain("executionStats")`` on a representative query and reports whether the
winning plan is an index scan (IXSCAN) rather than a collection scan (COLLSCAN).

Run:  python -m scripts.create_indexes
  (or: python scripts/create_indexes.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a plain file (python scripts/create_indexes.py) as well as a
# module (python -m scripts.create_indexes) by ensuring the project root is on
# the import path before importing the app package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import ASCENDING, DESCENDING  # noqa: E402

from app.db.mongo import get_mongo  # noqa: E402


def _winning_plan_stages(explain: dict) -> list[str]:
    """Collect every stage name in the winning plan tree.

    The winning plan is a nested tree (a stage plus ``inputStage`` /
    ``inputStages`` children). On MongoDB 7 the classic tree may also be wrapped
    under ``queryPlan`` (slot-based execution). Walk all of those so we detect an
    IXSCAN wherever it sits (e.g. FETCH -> IXSCAN, or SORT -> FETCH -> IXSCAN).
    """
    root = explain["queryPlanner"]["winningPlan"]
    stages: list[str] = []
    frontier = [root]
    while frontier:
        node = frontier.pop()
        if not isinstance(node, dict):
            continue
        stage = node.get("stage") or node.get("queryPlan", {}).get("stage")
        if stage:
            stages.append(stage)
        if "queryPlan" in node:
            frontier.append(node["queryPlan"])
        if "inputStage" in node:
            frontier.append(node["inputStage"])
        frontier.extend(node.get("inputStages", []))
    return stages


def main() -> None:
    db = get_mongo()
    runbooks = db["runbooks"]
    exec_log = db["execution_log"]

    # Compound index following ESR (Equality, Sort, Range) ordering:
    #   Equality: category, severity  -> ascending prefix the query filters on
    #   Sort:     updated_at          -> descending, matching .sort({updated_at:-1})
    # so a query filtering category+severity and sorting by updated_at can be
    # served entirely from the index (no in-memory SORT stage).
    runbooks.create_index(
        [("category", ASCENDING), ("severity", ASCENDING), ("updated_at", DESCENDING)]
    )

    # Slug is the natural key for a runbook; enforce uniqueness. (Default name
    # slug_1 matches the index the seed script already creates, so this is a
    # no-op rather than a conflicting re-declaration.)
    runbooks.create_index("slug", unique=True)

    # execution_log is queried by which runbook was run.
    exec_log.create_index("runbook_id")

    explain = db.command(
        "explain",
        {
            "find": "runbooks",
            "filter": {"category": "postgresql", "severity": "incident"},
            "sort": {"updated_at": -1},
        },
        verbosity="executionStats",
    )

    stages = _winning_plan_stages(explain)
    is_ixscan = "IXSCAN" in stages
    print(f"winning plan stages: {stages}")
    print(f"winning plan is IXSCAN: {is_ixscan}")


if __name__ == "__main__":
    main()
