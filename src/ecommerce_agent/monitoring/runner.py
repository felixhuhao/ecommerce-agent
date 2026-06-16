from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.cause import explain_finding
from ecommerce_agent.monitoring.checks import MonitorCheck
from ecommerce_agent.monitoring.grounding import build_alert_grounding
from ecommerce_agent.monitoring.models import Alert, Finding
from ecommerce_agent.monitoring.reader import MonitorReader
from ecommerce_agent.monitoring.store import AlertStore


async def run_monitor_cycle(
    *,
    reader: MonitorReader,
    checks: Sequence[MonitorCheck],
    alert_store: AlertStore,
    settings: Settings,
    bus: Any | None = None,
    cause_agent: Any | None = None,
) -> dict[str, Any]:
    created: list[Alert] = []
    skipped = 0
    errors: list[dict[str, str]] = []

    for check in checks:
        try:
            findings = await check.run(reader)
        except Exception as exc:
            errors.append({"check": check.name, "error": f"{type(exc).__name__}: {exc}"})
            continue

        for finding in findings:
            if await _should_skip(finding, alert_store, settings):
                skipped += 1
                continue
            cause, cause_record, diagnostic = await explain_finding(
                agent=cause_agent,
                finding=finding,
                settings=settings,
            )
            alert = Alert(
                check_name=finding.check_name,
                dedupe_key=finding.dedupe_key,
                title=finding.title,
                severity=finding.severity,
                metric=finding.metric,
                value=finding.value,
                threshold=finding.threshold,
                entities=finding.entities,
                cause=cause,
                grounding=build_alert_grounding(
                    finding,
                    cause_record=cause_record,
                    diagnostic=diagnostic,
                ),
            )
            created.append(await alert_store.create(alert))
            if bus is not None:
                bus.publish({"event": "alert.created", "alert": alert.model_dump(mode="json")})

    return {
        "status": "ok",
        "created": [alert.model_dump(mode="json") for alert in created],
        "created_count": len(created),
        "skipped_count": skipped,
        "errors": errors,
    }


async def _should_skip(
    finding: Finding,
    alert_store: AlertStore,
    settings: Settings,
) -> bool:
    if await alert_store.open_for_dedupe_key(finding.dedupe_key):
        return True
    latest = await alert_store.latest_for_dedupe_key(finding.dedupe_key)
    if latest is None:
        return False
    if settings.monitor_cooldown_seconds <= 0:
        return False
    cooldown_anchor = latest.acknowledged_at or latest.updated_at or latest.created_at
    age = datetime.now(UTC) - _parse_iso(cooldown_anchor)
    return age.total_seconds() < settings.monitor_cooldown_seconds


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
