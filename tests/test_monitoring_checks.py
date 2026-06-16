from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.checks import LowStockCheck, SalesDropWowCheck, build_default_checks
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


def test_default_check_registry_uses_settings() -> None:
    settings = Settings(
        _env_file=None,
        monitor_low_stock_threshold=12,
        monitor_sales_drop_pct=0.4,
    )

    checks = build_default_checks(settings)

    assert [check.name for check in checks] == ["low_stock", "sales_drop_wow"]
    assert checks[0].threshold == 12
    assert checks[1].drop_pct == 0.4
