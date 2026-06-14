from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ecommerce_agent.grounding.model import Authority


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_alert_id() -> str:
    return str(uuid.uuid4())


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertSource(BaseModel):
    source_id: str
    tool_name: str
    args_summary: str | None = None
    result_summary: str | None = None
    evidence: str | None = None


class AlertGrounding(BaseModel):
    authority: Authority
    sources: list[AlertSource] = Field(default_factory=list)
    diagnostic: str | None = None


class FindingEvidence(BaseModel):
    source_id: str
    tool_name: str
    args_summary: str | None = None
    result_summary: str | None = None
    evidence: str | None = None


class Finding(BaseModel):
    check_name: str
    dedupe_key: str
    title: str
    severity: AlertSeverity = AlertSeverity.WARNING
    metric: str
    value: float | int | str | None = None
    threshold: float | int | str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    evidence: list[FindingEvidence] = Field(default_factory=list)


class Alert(BaseModel):
    alert_id: str = Field(default_factory=new_alert_id)
    check_name: str
    dedupe_key: str
    title: str
    severity: AlertSeverity = AlertSeverity.WARNING
    status: AlertStatus = AlertStatus.OPEN
    metric: str
    value: float | int | str | None = None
    threshold: float | int | str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    cause: str | None = None
    grounding: AlertGrounding
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None

