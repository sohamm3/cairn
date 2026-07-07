"""Read-side aggregation queries over Cairn's MongoDB collections."""

from __future__ import annotations

from app.db.mongo import get_mongo


def runbook_stats_by_category() -> list[dict]:
    """Summarise incident runbooks per category.

    Pipeline: keep only ``incident`` runbooks, join each to its owner in the
    users collection, then group by category to count the runbooks and collect
    the distinct owner names, ordered by count (busiest category first).
    """
    db = get_mongo()
    pipeline = [
        {"$match": {"severity": "incident"}},
        {
            "$lookup": {
                "from": "users",
                "localField": "owner_id",
                "foreignField": "_id",
                "as": "owner",
            }
        },
        {
            "$group": {
                "_id": "$category",
                "count": {"$sum": 1},
                # owner is a single-element array from $lookup; take its name.
                "owner_names": {"$addToSet": {"$arrayElemAt": ["$owner.name", 0]}},
            }
        },
        {"$sort": {"count": -1}},
        {
            "$project": {
                "_id": 0,
                "category": "$_id",
                "count": 1,
                "owner_names": 1,
            }
        },
    ]
    return list(db["runbooks"].aggregate(pipeline))


if __name__ == "__main__":
    from pprint import pprint

    pprint(runbook_stats_by_category())
