from __future__ import annotations

import logging
from typing import Any

from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.models import Finding
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord

logger = logging.getLogger(__name__)


async def explain_finding(
    *,
    agent: Any | None,
    finding: Finding,
    settings: Settings,
) -> tuple[str | None, TraceRecord | None, str | None]:
    if agent is None:
        return None, None, None

    prompt = _finding_prompt(finding)
    record = TraceRecord(
        session_id=settings.monitor_spring_session_id,
        actor={"kind": "monitor", "spring_user_id": settings.monitor_spring_user_id},
    )
    try:
        raw_events = agent.astream_events(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"recursion_limit": min(settings.agent_recursion_limit, 20)},
            version="v2",
        )
        async for _event in capture(
            raw_events,
            record,
            evidence_max_chars=settings.grounding_evidence_max_chars,
        ):
            pass
        record.finish()
        return record.answer or None, record, None
    except Exception as exc:
        logger.warning("monitor cause pass failed for %s", finding.dedupe_key, exc_info=True)
        return None, None, f"cause_error:{type(exc).__name__}"


def _finding_prompt(finding: Finding) -> str:
    return (
        "Explain the likely operational cause for this alert in one short paragraph. "
        "Use only available read tools if more context is required. Do not propose, "
        "approve, execute, write files, create charts, or inspect filesystem state.\n\n"
        f"Alert: {finding.title}\n"
        f"Metric: {finding.metric}\n"
        f"Value: {finding.value}\n"
        f"Threshold: {finding.threshold}\n"
        f"Entities: {finding.entities}\n"
        f"Detection evidence: {[item.model_dump() for item in finding.evidence]}"
    )
