from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ecommerce_agent.routing.registry import build_specialist_registry

_DATASET_PATH = Path(__file__).parent / "datasets" / "routing.yaml"


@dataclass(frozen=True)
class RoutingCase:
    id: str
    prompt: str
    expected: str
    tags: list[str] = field(default_factory=list)


def load_routing_cases(path: str | None = None) -> list[RoutingCase]:
    target = Path(path) if path else _DATASET_PATH
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or []
    registry = build_specialist_registry()
    cases: list[RoutingCase] = []
    for entry in raw:
        case = RoutingCase(
            id=entry["id"],
            prompt=entry["prompt"],
            expected=entry["expected"],
            tags=list(entry.get("tags", [])),
        )
        if not registry.is_registered(case.expected):
            raise ValueError(f"case {case.id!r} has unknown specialist {case.expected!r}")
        cases.append(case)
    return cases
