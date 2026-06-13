from ecommerce_agent.auth.models import Action, Role
from ecommerce_agent.auth.permissions import can


def test_operator_can_everything_gated():
    assert can(Role.OPERATOR, Action.PROPOSE)
    assert can(Role.OPERATOR, Action.APPROVE)
    assert can(Role.OPERATOR, Action.AUDIT_SEARCH)


def test_viewer_can_nothing_gated():
    assert not can(Role.VIEWER, Action.PROPOSE)
    assert not can(Role.VIEWER, Action.APPROVE)
    assert not can(Role.VIEWER, Action.AUDIT_SEARCH)
