from datetime import UTC, datetime

from ecommerce_agent.config import Settings
from ecommerce_agent.grounding.model import Authority
from ecommerce_agent.monitoring.checks import (
    LowStockCheck,
    SalesDropWowCheck,
    StaleOrderCheck,
    build_default_checks,
)
from ecommerce_agent.monitoring.grounding import build_alert_grounding
from ecommerce_agent.monitoring.models import Finding, FindingEvidence
from ecommerce_agent.monitoring.reader import InMemoryMonitorReader, McpMonitorReader


async def test_low_stock_check_emits_findings_with_canonical_evidence() -> None:
    reader = InMemoryMonitorReader(
        low_stock_rows=[
            {"sku": "SKU-9", "name": "Power Bank", "quantity": 12},
            {"sku": "SKU-10", "name": "Cable", "quantity": 80},
        ]
    )

    findings = await LowStockCheck(threshold=50).run(reader)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.dedupe_key == "low_stock:SKU-9"
    assert finding.value == 12
    assert finding.threshold == 50
    assert finding.evidence[0].tool_name == "inventory_low_stock"


async def test_low_stock_check_handles_safety_stock_rows() -> None:
    reader = InMemoryMonitorReader(
        low_stock_rows=[
            {
                "productId": 22,
                "quantity": 55,
                "safetyStock": 96,
                "shortage": 41,
                "warehouse": "B区",
            }
        ]
    )

    findings = await LowStockCheck(threshold=50).run(reader)

    assert findings[0].dedupe_key == "low_stock:22:B区"
    assert findings[0].title == "Low stock: Product 22 (B区)"
    assert findings[0].value == 55
    assert findings[0].threshold == 96


async def test_sales_drop_wow_accepts_explicit_or_derived_drop_rate() -> None:
    reader = InMemoryMonitorReader(
        sales_drop_rows=[
            {"category": "Electronics", "current": 70, "previous": 100},
            {"category": "Books", "drop_pct": 0.1},
        ]
    )

    findings = await SalesDropWowCheck(drop_pct=0.25).run(reader)

    assert [finding.dedupe_key for finding in findings] == ["sales_drop_wow:Electronics"]
    assert findings[0].value == 0.3
    assert findings[0].evidence[0].tool_name == "get_statistics"


async def test_mcp_monitor_reader_extracts_json_rows_from_content_wrappers() -> None:
    class Tool:
        name = "inventory_low_stock"

        async def ainvoke(self, args):  # noqa: ANN001
            assert args == {"threshold": 50}
            return [
                {
                    "type": "text",
                    "text": '[{"productId":22,"quantity":55,"safetyStock":96}]',
                    "id": "lc_wrapper",
                }
            ]

    rows, evidence = await McpMonitorReader([Tool()]).inventory_low_stock(threshold=50)

    assert rows == [{"productId": 22, "quantity": 55, "safetyStock": 96}]
    assert evidence.tool_name == "inventory_low_stock"


async def test_stale_order_check_uses_status_specific_timestamps() -> None:
    now = datetime(2026, 6, 19, 12, tzinfo=UTC)
    reader = InMemoryMonitorReader(
        stale_pending_rows=[
            {
                "orderId": 1008,
                "userId": 7,
                "status": "pending",
                "createdAt": "2026-06-16T12:00:00+00:00",
                "totalAmount": 99.5,
            },
        ],
        stale_paid_rows=[
            {
                "orderId": 1012,
                "userId": 9,
                "status": "paid",
                "createdAt": "2026-06-16T12:00:00+00:00",
                "paidAt": "2026-06-18T18:00:00+00:00",
            },
        ],
    )

    findings = await StaleOrderCheck(
        pending_hours=48,
        paid_hours=12,
        now_fn=lambda: now,
    ).run(reader)

    assert [finding.dedupe_key for finding in findings] == [
        "stale_order:pending:1008",
        "stale_order:paid:1012",
    ]
    assert findings[0].value == 72
    assert findings[0].entities["createdAt"] == "2026-06-16T12:00:00+00:00"
    assert findings[0].evidence[0].source_id == "detection:order_query:pending:1008"
    assert findings[1].value == 18
    assert findings[1].entities["paidAt"] == "2026-06-18T18:00:00+00:00"
    assert findings[1].entities["createdAt"] == "2026-06-16T12:00:00+00:00"
    assert findings[1].evidence[0].source_id == "detection:order_query:paid:1012"


async def test_stale_order_check_trusts_server_prefiltered_candidates() -> None:
    reader = InMemoryMonitorReader(
        stale_pending_rows=[
            {
                "orderId": 1009,
                "status": "pending",
                "createdAt": "2026-06-19T08:00:00+00:00",
            }
        ]
    )

    findings = await StaleOrderCheck(
        pending_hours=48,
        paid_hours=12,
        now_fn=lambda: datetime(2026, 6, 19, 12, tzinfo=UTC),
    ).run(reader)

    assert [finding.dedupe_key for finding in findings] == ["stale_order:pending:1009"]
    assert findings[0].value == 4
    assert findings[0].threshold == 48


async def test_stale_order_check_treats_naive_timestamps_as_local_time() -> None:
    reader = InMemoryMonitorReader(
        stale_pending_rows=[
            {
                "orderId": 1010,
                "status": "pending",
                "createdAt": "2026-06-19T08:00:00",
            }
        ]
    )

    findings = await StaleOrderCheck(
        pending_hours=48,
        paid_hours=12,
        now_fn=lambda: datetime(2026, 6, 19, 12),
    ).run(reader)

    assert [finding.dedupe_key for finding in findings] == ["stale_order:pending:1010"]
    assert findings[0].value == 4


async def test_stale_order_check_skips_paid_rows_without_paid_at() -> None:
    reader = InMemoryMonitorReader(
        stale_paid_rows=[
            {
                "orderId": 1014,
                "status": "paid",
                "createdAt": "2026-06-10T12:00:00",
            }
        ]
    )

    findings = await StaleOrderCheck(
        pending_hours=48,
        paid_hours=12,
        now_fn=lambda: datetime(2026, 6, 19, 12, tzinfo=UTC),
    ).run(reader)

    assert findings == []


async def test_mcp_monitor_reader_invokes_order_query_with_stale_filter() -> None:
    class Tool:
        name = "order_query"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def ainvoke(self, args):  # noqa: ANN001
            self.calls.append(args)
            return [{"orderId": 1008, "status": args["status"]}]

    tool = Tool()
    reader = McpMonitorReader([tool])

    rows, evidence = await reader.stale_pending_order_candidates(older_than_hours=48)

    assert rows == [{"orderId": 1008, "status": "pending"}]
    assert tool.calls == [
        {"status": "pending", "staleOlderThanHours": 48, "limit": 50}
    ]
    assert evidence.tool_name == "order_query"
    assert "status" in evidence.args_summary


async def test_mcp_monitor_reader_invokes_paid_order_query_with_stale_filter() -> None:
    class Tool:
        name = "order_query"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def ainvoke(self, args):  # noqa: ANN001
            self.calls.append(args)
            return [{"orderId": 1012, "status": args["status"]}]

    tool = Tool()
    reader = McpMonitorReader([tool])

    rows, evidence = await reader.stale_paid_order_candidates(older_than_hours=24)

    assert rows == [{"orderId": 1012, "status": "paid"}]
    assert tool.calls == [{"status": "paid", "staleOlderThanHours": 24, "limit": 50}]
    assert evidence.tool_name == "order_query"
    assert "paid" in evidence.args_summary


def test_order_query_detection_is_authoritative() -> None:
    grounding = build_alert_grounding(
        Finding(
            check_name="stale_order",
            dedupe_key="stale_order:pending:1008",
            title="Stale pending order: 1008",
            metric="stale_order_age_hours",
            evidence=[
                FindingEvidence(
                    source_id="detection:order_query:pending:1008",
                    tool_name="order_query",
                )
            ],
        )
    )

    assert grounding.authority == Authority.AUTHORITATIVE


def test_default_check_registry_uses_settings() -> None:
    settings = Settings(
        _env_file=None,
        monitor_low_stock_threshold=12,
        monitor_sales_drop_pct=0.4,
        monitor_stale_pending_order_hours=72,
        monitor_stale_paid_order_hours=36,
    )

    checks = build_default_checks(settings)

    assert [check.name for check in checks] == [
        "low_stock",
        "sales_drop_wow",
        "stale_order",
    ]
    assert checks[0].threshold == 12
    assert checks[1].drop_pct == 0.4
    assert checks[2].pending_hours == 72
    assert checks[2].paid_hours == 36
