from __future__ import annotations

import dataclasses
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class TraceEvent:
    """A span-like event. IDs are shaped so OTel export can be a later projection."""

    event_type: str
    name: str | None = None
    span_id: str = field(default_factory=new_id)
    parent_span_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    phase: str | None = None
    status: str = "ok"
    ts: float = field(default_factory=time.time)
    duration_ms: float | None = None
    args_summary: str | None = None
    result_summary: str | None = None
    evidence: str | None = None
    error_message: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model_call_id: str | None = None
    tool_call_id: str | None = None
    sandbox_exec_id: str | None = None
    artifact_id: str | None = None
    artifact: dict[str, Any] | None = None
    approval_id: str | None = None
    execution_id: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceEvent:
        names = {field.name for field in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})


@dataclass
class TraceRecord:
    """One chat turn or live-eval attempt."""

    trace_id: str = field(default_factory=new_id)
    schema_version: str = SCHEMA_VERSION
    session_id: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    actor: dict | None = None
    model: dict | None = None
    prompt_version: str | None = None
    git_commit: str | None = None
    dependency_versions: dict | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    duration_ms: float | None = None
    answer: str = ""
    events: list[TraceEvent] = field(default_factory=list)

    def finish(self) -> None:
        self.ended_at = time.time()
        self.duration_ms = (self.ended_at - self.started_at) * 1000.0

    def tool_names(self) -> list[str]:
        return [
            event.name
            for event in self.events
            if event.event_type == "tool_call" and event.phase == "start" and event.name
        ]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceRecord:
        names = {field.name for field in dataclasses.fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in names and key != "events"}
        events = [TraceEvent.from_dict(event) for event in data.get("events", [])]
        return cls(events=events, **kwargs)
