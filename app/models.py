"""Pydantic schemas: API input/output and the LLM extraction schema."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["billing", "shipping", "refund", "technical", "account", "other"]
Urgency = Literal["low", "medium", "high"]


class EmailIn(BaseModel):
    sender: str = Field(min_length=1, max_length=320)
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=100_000)


class EnqueueOut(BaseModel):
    job_id: int
    status: str
    duplicate: bool


class Extraction(BaseModel):
    """What we require from the LLM. Anything else is rejected and retried."""

    model_config = ConfigDict(extra="ignore")

    category: Category
    urgency: Urgency
    customer_name: str | None = None
    order_id: str | None = None
    summary: str = Field(min_length=1)
    suggested_reply: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("customer_name", "order_id", mode="before")
    @classmethod
    def clean_optional(cls, v):
        # Models sometimes return "", "null" or a number instead of JSON null.
        if v is None:
            return None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if isinstance(v, str):
            v = v.strip()
            if v.lower() in ("", "null", "none", "n/a"):
                return None
        return v


class ResultOut(BaseModel):
    id: int
    job_id: int
    category: str | None = None
    urgency: str | None = None
    customer_name: str | None = None
    order_id: str | None = None
    summary: str | None = None
    suggested_reply: str | None = None
    confidence: float | None = None
    model: str | None = None
    latency_ms: int | None = None


class JobOut(BaseModel):
    id: int
    email_id: int
    status: str
    attempts: int
    max_attempts: int
    next_run_at: float
    last_error: str | None = None
    result: ResultOut | None = None


class JobListItem(BaseModel):
    id: int
    email_id: int
    status: str
    attempts: int
    max_attempts: int
    next_run_at: float
    last_error: str | None = None


class MetricsOut(BaseModel):
    counts: dict[str, int]
    total: int
    queue_depth: int
    pending_review: int
    avg_attempts: float | None = None
