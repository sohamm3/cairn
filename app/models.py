"""Pydantic models mirroring Cairn's MongoDB schema.

Storage vs. API boundary: MongoDB stores real BSON ObjectIds, but these
API-facing models type every id / reference field as ``str`` (the 24-char hex
form) so the FastAPI layer serializes them as plain JSON strings. The seed
script writes native ObjectIds directly; conversion to these models happens at
the API edge.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Category(str, Enum):
    postgresql = "postgresql"
    mongodb = "mongodb"
    mysql = "mysql"
    redis = "redis"


class Severity(str, Enum):
    routine = "routine"
    incident = "incident"
    maintenance = "maintenance"


class Outcome(str, Enum):
    success = "success"
    failed = "failed"
    aborted = "aborted"


class Step(BaseModel):
    """An embedded step within a runbook."""

    order: int
    instruction: str
    command: str | None = None
    expected_result: str | None = None


class User(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    name: str
    email: str
    team: str


class Runbook(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    title: str
    slug: str
    category: Category
    severity: Severity
    summary: str
    steps: list[Step] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    related_runbook_ids: list[str] = Field(default_factory=list)
    owner_id: str
    created_at: datetime
    updated_at: datetime


class ExecutionLog(BaseModel):
    """A single runbook execution. Stored in its own unbounded collection."""

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    runbook_id: str
    operator_id: str
    ran_at: datetime
    outcome: Outcome
