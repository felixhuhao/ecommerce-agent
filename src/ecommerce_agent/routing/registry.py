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
        names = [s.name for s in specialists]
        if len(set(names)) != len(names):
            raise ValueError("registry specialist names must be unique")
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
    """Build the registry from the authoritative provider list in specialists.providers.

    ``PROVIDERS`` is imported lazily so this module stays importable without pulling
    in the agent builders / DeepAgents runtime wiring — the descriptor registry is
    meant to stay lightweight for routing and eval code.
    """
    from ecommerce_agent.specialists.providers import PROVIDERS

    return SpecialistRegistry(
        [Specialist(p.name, p.description, default=p.default) for p in PROVIDERS]
    )
