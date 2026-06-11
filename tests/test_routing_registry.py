import pytest

from ecommerce_agent.routing.registry import (
    Specialist,
    SpecialistRegistry,
    build_specialist_registry,
)


def test_default_specialist_is_the_flagged_one() -> None:
    reg = build_specialist_registry()
    assert reg.default.name == "sales-analyst"
    assert set(reg.names()) == {"sales-analyst", "order-manager"}
    assert reg.is_registered("order-manager") is True
    assert reg.is_registered("unsure") is False


def test_describe_lists_names_and_descriptions() -> None:
    reg = build_specialist_registry()
    text = reg.describe()
    assert "sales-analyst:" in text
    assert "order-manager:" in text


def test_registry_requires_exactly_one_default() -> None:
    with pytest.raises(ValueError):
        SpecialistRegistry([Specialist("a", "x", default=False)])
    with pytest.raises(ValueError):
        SpecialistRegistry([Specialist("a", "x", default=True), Specialist("b", "y", default=True)])
