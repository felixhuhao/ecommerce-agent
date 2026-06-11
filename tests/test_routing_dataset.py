import pytest

from ecommerce_agent.evals.routing import RoutingCase, load_routing_cases


def test_dataset_loads_and_is_well_formed() -> None:
    cases = load_routing_cases()

    assert len(cases) >= 10
    assert all(isinstance(c, RoutingCase) for c in cases)
    assert all(c.expected in {"sales-analyst", "order-manager"} for c in cases)
    assert sum("adversarial" in c.tags for c in cases) >= 4


def test_loader_rejects_unknown_specialist(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: x\n  prompt: hi\n  expected: wizard\n  tags: []\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_routing_cases(str(bad))
