from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Authority(StrEnum):
    AUTHORITATIVE = "authoritative"
    DERIVED = "derived"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class GroundingSource:
    span_id: str
    tool_name: str
    args_summary: str | None = None
    result_summary: str | None = None


@dataclass
class Grounding:
    authority: Authority
    sources: list[GroundingSource] = field(default_factory=list)
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority": self.authority.value,
            "sources": [dataclasses.asdict(source) for source in self.sources],
            "diagnostic": self.diagnostic,
        }
