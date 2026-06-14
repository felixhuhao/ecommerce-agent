from datetime import UTC, datetime, timedelta

from ecommerce_agent.config import Settings
from ecommerce_agent.grounding.model import Authority
from ecommerce_agent.monitoring.models import (
    Alert,
    AlertGrounding,
    AlertSource,
    Finding,
    FindingEvidence,
)
from ecommerce_agent.monitoring.reader import InMemoryMonitorReader
from ecommerce_agent.monitoring.runner import run_monitor_cycle
from ecommerce_agent.monitoring.store import InMemoryAlertStore


class StaticCheck:
    name = "low_stock"

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings

    async def run(self, _reader) -> list[Finding]:  # noqa: ANN001
        return self.findings


class FailingCheck:
    name = "low_stock"

    async def run(self, _reader) -> list[Finding]:  # noqa: ANN001
        raise RuntimeError("backend unavailable")


class FailingCauseAgent:
    async def astream_events(self, inputs: dict, config: dict, version: str):  # noqa: ANN001
        raise RuntimeError("llm unavailable")
        yield  # pragma: no cover


class SpyBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)


def finding() -> Finding:
    return Finding(
        check_name="low_stock",
        dedupe_key="low_stock:SKU-9",
        title="Low stock: SKU-9",
        metric="inventory",
        value=12,
        threshold=50,
        evidence=[
            FindingEvidence(
                source_id="detection:inventory_low_stock:SKU-9",
                tool_name="inventory_low_stock",
                result_summary="12 units",
                evidence='{"sku":"SKU-9","quantity":12}',
            )
        ],
    )


async def test_run_monitor_cycle_creates_authoritative_alert_and_publishes() -> None:
    store = InMemoryAlertStore()
    bus = SpyBus()

    result = await run_monitor_cycle(
        reader=InMemoryMonitorReader(),
        checks=[StaticCheck([finding()])],
        alert_store=store,
        settings=Settings(_env_file=None),
        bus=bus,
    )

    assert result["created_count"] == 1
    alert = result["created"][0]
    assert alert["grounding"]["authority"] == Authority.AUTHORITATIVE
    assert alert["grounding"]["sources"][0]["evidence"]
    assert bus.events[0]["event"] == "alert.created"


async def test_run_monitor_cycle_skips_still_open_duplicate() -> None:
    store = InMemoryAlertStore()
    first = finding()
    await store.create(
        Alert(
            check_name=first.check_name,
            dedupe_key=first.dedupe_key,
            title=first.title,
            metric=first.metric,
            grounding=AlertGrounding(
                authority=Authority.AUTHORITATIVE,
                sources=[AlertSource(source_id="x", tool_name="inventory_low_stock")],
            ),
        )
    )

    result = await run_monitor_cycle(
        reader=InMemoryMonitorReader(),
        checks=[StaticCheck([first])],
        alert_store=store,
        settings=Settings(_env_file=None),
    )

    assert result["created_count"] == 0
    assert result["skipped_count"] == 1


async def test_acknowledged_alert_repeats_after_cooldown() -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    store = InMemoryAlertStore(now=lambda: now)
    first = finding()
    alert = await store.create(
        Alert(
            check_name=first.check_name,
            dedupe_key=first.dedupe_key,
            title=first.title,
            metric=first.metric,
            created_at=(now - timedelta(hours=2)).isoformat(),
            grounding=AlertGrounding(
                authority=Authority.AUTHORITATIVE,
                sources=[AlertSource(source_id="x", tool_name="inventory_low_stock")],
            ),
        )
    )
    await store.acknowledge(alert.alert_id, actor_id="op1")

    result = await run_monitor_cycle(
        reader=InMemoryMonitorReader(),
        checks=[StaticCheck([first])],
        alert_store=store,
        settings=Settings(_env_file=None, monitor_cooldown_seconds=60),
    )

    assert result["created_count"] == 1


async def test_check_error_is_reported_and_skipped() -> None:
    result = await run_monitor_cycle(
        reader=InMemoryMonitorReader(),
        checks=[FailingCheck()],
        alert_store=InMemoryAlertStore(),
        settings=Settings(_env_file=None),
    )

    assert result["created_count"] == 0
    assert result["errors"] == [
        {"check": "low_stock", "error": "RuntimeError: backend unavailable"}
    ]


async def test_cause_error_keeps_authoritative_detection_grounding() -> None:
    result = await run_monitor_cycle(
        reader=InMemoryMonitorReader(),
        checks=[StaticCheck([finding()])],
        alert_store=InMemoryAlertStore(),
        settings=Settings(_env_file=None),
        cause_agent=FailingCauseAgent(),
    )

    alert = result["created"][0]
    assert alert["cause"] is None
    assert alert["grounding"]["authority"] == Authority.AUTHORITATIVE
    assert alert["grounding"]["diagnostic"] == "cause_error:RuntimeError"


async def test_acknowledged_alert_within_cooldown_is_skipped() -> None:
    now = datetime.now(UTC)
    store = InMemoryAlertStore(now=lambda: now)
    first = finding()
    alert = await store.create(
        Alert(
            check_name=first.check_name,
            dedupe_key=first.dedupe_key,
            title=first.title,
            metric=first.metric,
            created_at=(now - timedelta(seconds=30)).isoformat(),
            grounding=AlertGrounding(
                authority=Authority.AUTHORITATIVE,
                sources=[AlertSource(source_id="x", tool_name="inventory_low_stock")],
            ),
        )
    )
    await store.acknowledge(alert.alert_id, actor_id="op1")

    result = await run_monitor_cycle(
        reader=InMemoryMonitorReader(),
        checks=[StaticCheck([first])],
        alert_store=store,
        settings=Settings(_env_file=None, monitor_cooldown_seconds=60),
    )

    assert result["created_count"] == 0
    assert result["skipped_count"] == 1
