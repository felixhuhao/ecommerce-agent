from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.models import AlertSeverity, Finding, FindingEvidence
from ecommerce_agent.monitoring.reader import MonitorReader


class MonitorCheck(Protocol):
    name: str

    async def run(self, reader: MonitorReader) -> list[Finding]:
        ...


class LowStockCheck:
    name = "low_stock"

    def __init__(self, *, threshold: int) -> None:
        self.threshold = threshold

    async def run(self, reader: MonitorReader) -> list[Finding]:
        rows, evidence = await reader.inventory_low_stock(threshold=self.threshold)
        findings: list[Finding] = []
        for row in rows:
            quantity = _first_number(row, ("quantity", "stock", "available", "currentStock"))
            if quantity is not None and quantity > self.threshold:
                continue
            product_key = _entity_key(row, ("productId", "product_id", "sku", "id", "name"))
            product_label = _entity_label(row, product_key)
            findings.append(
                Finding(
                    check_name=self.name,
                    dedupe_key=f"low_stock:{product_key}",
                    title=f"Low stock: {product_label}",
                    severity=AlertSeverity.WARNING,
                    metric="inventory",
                    value=quantity,
                    threshold=self.threshold,
                    entities=_entities(row),
                    evidence=[_scoped_evidence(evidence, product_key)],
                )
            )
        return findings


class SalesDropWowCheck:
    name = "sales_drop_wow"

    def __init__(self, *, drop_pct: float) -> None:
        self.drop_pct = drop_pct

    async def run(self, reader: MonitorReader) -> list[Finding]:
        rows, evidence = await reader.sales_drop_wow()
        findings: list[Finding] = []
        for row in rows:
            drop_pct = _drop_pct(row)
            if drop_pct is None or drop_pct < self.drop_pct:
                continue
            entity_key = _entity_key(row, ("category", "productId", "product_id", "sku", "id"))
            entity_label = _entity_label(row, entity_key)
            findings.append(
                Finding(
                    check_name=self.name,
                    dedupe_key=f"sales_drop_wow:{entity_key}",
                    title=f"Sales drop: {entity_label}",
                    severity=AlertSeverity.WARNING,
                    metric="sales_drop_wow",
                    value=round(drop_pct, 4),
                    threshold=self.drop_pct,
                    entities=_entities(row),
                    evidence=[_scoped_evidence(evidence, entity_key)],
                )
            )
        return findings


def build_default_checks(settings: Settings) -> list[MonitorCheck]:
    return [
        LowStockCheck(threshold=settings.monitor_low_stock_threshold),
        SalesDropWowCheck(drop_pct=settings.monitor_sales_drop_pct),
    ]


def _first_number(row: dict[str, Any], keys: Sequence[str]) -> float | int | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return value
    return None


def _drop_pct(row: dict[str, Any]) -> float | None:
    explicit = _first_number(row, ("drop_pct", "dropPercent", "drop_rate", "dropRate"))
    if explicit is not None:
        return explicit / 100 if explicit > 1 else float(explicit)

    current = _first_number(row, ("current", "current_sales", "currentSales"))
    previous = _first_number(row, ("previous", "previous_sales", "previousSales", "prior"))
    if current is None or previous in (None, 0):
        return None
    return max(0.0, (float(previous) - float(current)) / float(previous))


def _entity_key(row: dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return "unknown"


def _entity_label(row: dict[str, Any], fallback: str) -> str:
    for key in ("name", "productName", "sku", "category"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


def _entities(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        in {
            "id",
            "productId",
            "product_id",
            "sku",
            "name",
            "productName",
            "category",
            "supplierId",
            "supplier_id",
        }
    }


def _scoped_evidence(evidence: FindingEvidence, entity_key: str) -> FindingEvidence:
    return evidence.model_copy(update={"source_id": f"{evidence.source_id}:{entity_key}"})

