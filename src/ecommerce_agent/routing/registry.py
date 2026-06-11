from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Specialist:
    name: str
    description: str
    default: bool = False


class SpecialistRegistry:
    """Descriptor-only registry shared by runtime and eval code."""

    def __init__(self, specialists: list[Specialist]) -> None:
        defaults = [s for s in specialists if s.default]
        if len(defaults) != 1:
            raise ValueError("registry requires exactly one default specialist")
        self.specialists = specialists

    def names(self) -> list[str]:
        return [s.name for s in self.specialists]

    @property
    def default(self) -> Specialist:
        return next(s for s in self.specialists if s.default)

    def is_registered(self, name: str) -> bool:
        return any(s.name == name for s in self.specialists)

    def describe(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.specialists)


def build_specialist_registry() -> SpecialistRegistry:
    return SpecialistRegistry(
        [
            Specialist(
                "sales-analyst",
                "read-only sales analytics: querying business data, trends, forecasts, and charts.",
                default=True,
            ),
            Specialist(
                "order-manager",
                "approval-only business writes: purchase orders, replenishment, "
                "receiving, and order-status changes.",
            ),
        ]
    )
