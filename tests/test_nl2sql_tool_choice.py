from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from ecommerce_agent.mcp_client import NL2SQL_TOOLS
from ecommerce_agent.tools.charting import CREATE_CHART_SPEC_TOOL_NAME
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord
from ecommerce_agent.trace.tools import fired_tools

COHORT_PROMPT = "show repeat purchase rate by customer cohort over the last 12 months"
WAREHOUSE_CHART_PROMPT = (
    "break down last 90 days revenue by region and channel as a chart"
)
CURRENT_STOCK_PROMPT = "is SKU-LOW-003 below safety stock?"
PO_PROMPT = "create a PO for productId 9 from supplier 7"


class ScriptedWarehouseHarness:
    """Deterministic stand-in for the routed specialist path.

    It locks the trace-level contract without invoking an LLM: warehouse analysis
    uses NL2SQL tools, warehouse charts also use create_chart_spec, and operational
    prompts do not call NL2SQL tools.
    """

    _TOOLS_BY_PROMPT = {
        COHORT_PROMPT: ["query_readonly"],
        WAREHOUSE_CHART_PROMPT: ["query_readonly", CREATE_CHART_SPEC_TOOL_NAME],
        CURRENT_STOCK_PROMPT: ["inventory_query"],
        PO_PROMPT: ["request_approval"],
    }

    async def astream_events(
        self,
        inputs: dict,
        *,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        prompt = inputs["messages"][0]["content"]
        for name in self._TOOLS_BY_PROMPT[prompt]:
            yield {"event": "on_tool_start", "name": name, "run_id": name, "data": {}}
            yield {
                "event": "on_tool_end",
                "name": name,
                "run_id": name,
                "data": {"output": {"ok": True, "tool": name}},
            }


async def _record_for(prompt: str) -> TraceRecord:
    record = TraceRecord()
    events = ScriptedWarehouseHarness().astream_events(
        {"messages": [{"role": "user", "content": prompt}]},
        config={},
        version="v2",
    )
    async for _ in capture(events, record):
        pass
    record.finish()
    return record


@pytest.mark.asyncio
async def test_warehouse_cohort_prompt_uses_query_readonly() -> None:
    record = await _record_for(COHORT_PROMPT)

    assert fired_tools(record) == ["query_readonly"]


@pytest.mark.asyncio
async def test_warehouse_chart_prompt_uses_query_then_echarts_spec() -> None:
    record = await _record_for(WAREHOUSE_CHART_PROMPT)

    assert fired_tools(record) == ["query_readonly", CREATE_CHART_SPEC_TOOL_NAME]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [CURRENT_STOCK_PROMPT, PO_PROMPT])
async def test_operational_prompts_do_not_call_nl2sql_tools(prompt: str) -> None:
    record = await _record_for(prompt)

    assert set(fired_tools(record)).isdisjoint(NL2SQL_TOOLS)

