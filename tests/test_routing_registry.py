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


def test_describe_is_byte_identical_to_the_classifier_prompt_snapshot() -> None:
    # Locking the exact router-facing text: the classifier prompt embeds this, so a
    # silent wording change would shift routing decisions without any test catching it.
    reg = build_specialist_registry()
    assert reg.describe() == (
        "- sales-analyst: read-only sales analytics: querying business data, trends, "
        "forecasts, and charts.\n"
        "- order-manager: approval-only business writes: purchase orders, "
        "replenishment, receiving, and order-status changes."
    )


def test_registry_derives_from_specialist_providers() -> None:
    from ecommerce_agent.specialists.providers import PROVIDERS

    reg = build_specialist_registry()
    assert reg.names() == [provider.name for provider in PROVIDERS]


def test_registry_requires_exactly_one_default() -> None:
    with pytest.raises(ValueError):
        SpecialistRegistry([Specialist("a", "x", default=False)])
    multiple_defaults = [
        Specialist("a", "x", default=True),
        Specialist("b", "y", default=True),
    ]
    with pytest.raises(ValueError):
        SpecialistRegistry(multiple_defaults)


def test_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="unique"):
        SpecialistRegistry(
            [
                Specialist("sales-analyst", "x", default=True),
                Specialist("sales-analyst", "y"),
            ]
        )
