from types import SimpleNamespace

from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.cause import explain_finding
from ecommerce_agent.monitoring.models import Finding, FindingEvidence


class ScriptedCauseAgent:
    async def astream_events(self, inputs: dict, config: dict, version: str):  # noqa: ANN001
        assert "Low stock: SKU-9" in inputs["messages"][0]["content"]
        assert config["recursion_limit"] == 20
        assert version == "v2"
        yield {
            "event": "on_tool_start",
            "name": "inventory_query",
            "run_id": "tool-1",
            "data": {"input": {"sku": "SKU-9"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "inventory_query",
            "run_id": "tool-1",
            "data": {"output": {"sku": "SKU-9", "quantity": 12}},
        }
        yield {
            "event": "on_chat_model_stream",
            "run_id": "model-1",
            "data": {"chunk": SimpleNamespace(content="Supplier delay is likely.")},
        }


def finding() -> Finding:
    return Finding(
        check_name="low_stock",
        dedupe_key="low_stock:SKU-9",
        title="Low stock: SKU-9",
        metric="inventory",
        evidence=[
            FindingEvidence(
                source_id="detection:inventory_low_stock:SKU-9",
                tool_name="inventory_low_stock",
                evidence='{"sku":"SKU-9","quantity":12}',
            )
        ],
    )


async def test_explain_finding_returns_answer_and_trace_record() -> None:
    answer, record, diagnostic = await explain_finding(
        agent=ScriptedCauseAgent(),
        finding=finding(),
        settings=Settings(_env_file=None),
    )

    assert answer == "Supplier delay is likely."
    assert diagnostic is None
    assert record is not None
    assert record.answer == "Supplier delay is likely."
    assert record.events[0].name == "inventory_query"

