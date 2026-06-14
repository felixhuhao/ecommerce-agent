from __future__ import annotations

from ecommerce_agent.auth.models import Action, Role

# Single source of truth for authorization. Add a role => add one entry.
_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset(),
    Role.OPERATOR: frozenset(
        {Action.PROPOSE, Action.APPROVE, Action.AUDIT_SEARCH, Action.MANAGE_ALERTS}
    ),
}


def can(role: Role, action: Action) -> bool:
    return action in _PERMISSIONS.get(role, frozenset())
