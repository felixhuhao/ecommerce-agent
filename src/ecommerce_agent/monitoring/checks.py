from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.models import AlertSeverity, Finding, FindingEvidence
from ecommerce_agent.monitoring.reader import MonitorReader

logger = logging.getLogger(__name__)


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
            safety_stock = _first_number(row, ("safetyStock", "safety_stock", "threshold"))
            shortage = _first_number(row, ("shortage", "gap"))
            if (
                safety_stock is None
                and shortage is None
                and quantity is not None
                and quantity > self.threshold
            ):
                continue
            product_key = _low_stock_key(row)
            product_label = _entity_label(row, product_key)
            findings.append(
                Finding(
                    check_name=self.name,
                    dedupe_key=f"low_stock:{product_key}",
                    title=f"Low stock: {product_label}",
                    severity=AlertSeverity.WARNING,
                    metric="inventory",
                    value=quantity,
                    threshold=safety_stock if safety_stock is not None else self.threshold,
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


class StaleOrderCheck:
    name = "stale_order"

    def __init__(
        self,
        *,
        pending_hours: int,
        paid_hours: int,
        max_per_status: int,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.pending_hours = pending_hours
        self.paid_hours = paid_hours
        self.max_per_status = max_per_status
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    async def run(self, reader: MonitorReader) -> list[Finding]:
        findings: list[Finding] = []
        pending_rows, pending_evidence = await reader.stale_pending_order_candidates(
            older_than_hours=self.pending_hours
        )
        findings.extend(
            self._findings_for_status(
                pending_rows,
                pending_evidence,
                status="pending",
                threshold_hours=self.pending_hours,
                anchor_key="createdAt",
                title_prefix="Stale pending order",
            )
        )

        paid_rows, paid_evidence = await reader.stale_paid_order_candidates(
            older_than_hours=self.paid_hours
        )
        findings.extend(
            self._findings_for_status(
                paid_rows,
                paid_evidence,
                status="paid",
                threshold_hours=self.paid_hours,
                anchor_key="paidAt",
                title_prefix="Paid order not shipped",
            )
        )
        return findings

    def _findings_for_status(
        self,
        rows: list[dict[str, Any]],
        evidence: FindingEvidence,
        *,
        status: str,
        threshold_hours: int,
        anchor_key: str,
        title_prefix: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for row in rows:
            row_status = str(row.get("status") or "").lower()
            if row_status and row_status != status:
                continue

            anchor = _timestamp(row, (anchor_key, _snake(anchor_key)))
            if anchor is None:
                logger.warning(
                    "skipping stale %s order without %s timestamp: %s",
                    status,
                    anchor_key,
                    _entity_key(row, ("orderId", "order_id", "id")),
                )
                continue

            age_hours = (_aware(self._now_fn()) - anchor).total_seconds() / 3600

            order_id = _entity_key(row, ("orderId", "order_id", "id"))
            entities = _entities(row) | {
                "ageHours": round(age_hours, 2),
                "status": status,
            }
            findings.append(
                Finding(
                    check_name=self.name,
                    dedupe_key=f"stale_order:{status}:{order_id}",
                    title=f"{title_prefix}: {order_id}",
                    severity=AlertSeverity.WARNING,
                    metric="stale_order_age_hours",
                    value=round(age_hours, 2),
                    threshold=threshold_hours,
                    entities=entities,
                    evidence=[_scoped_evidence(evidence, order_id)],
                )
            )
            if len(findings) >= self.max_per_status:
                break
        return findings


def build_default_checks(settings: Settings) -> list[MonitorCheck]:
    return [
        LowStockCheck(threshold=settings.monitor_low_stock_threshold),
        SalesDropWowCheck(drop_pct=settings.monitor_sales_drop_pct),
        StaleOrderCheck(
            pending_hours=settings.monitor_stale_pending_order_hours,
            paid_hours=settings.monitor_stale_paid_order_hours,
            max_per_status=settings.monitor_stale_order_max_per_status,
        ),
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
    explicit = _first_number(row, ("drop_pct", "dropPct", "dropPercent", "drop_rate", "dropRate"))
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


def _timestamp(row: dict[str, Any], keys: Sequence[str]) -> datetime | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=_local_timezone())
        if isinstance(value, str) and value.strip():
            parsed = _parse_timestamp(value)
            if parsed is not None:
                return parsed
    return None


def _parse_timestamp(value: str) -> datetime | None:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_local_timezone())


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=_local_timezone())


def _local_timezone():
    return datetime.now().astimezone().tzinfo or UTC


def _snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper() and chars:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def _low_stock_key(row: dict[str, Any]) -> str:
    product_key = _entity_key(row, ("productId", "product_id", "sku", "id", "name"))
    warehouse = row.get("warehouse")
    if warehouse not in (None, ""):
        return f"{product_key}:{warehouse}"
    return product_key


def _entity_label(row: dict[str, Any], fallback: str) -> str:
    for key in ("name", "productName", "sku", "category"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    product_id = row.get("productId") or row.get("product_id")
    if product_id not in (None, ""):
        warehouse = row.get("warehouse")
        suffix = f" ({warehouse})" if warehouse not in (None, "") else ""
        return f"Product {product_id}{suffix}"
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
            "quantity",
            "safetyStock",
            "safety_stock",
            "shortage",
            "warehouse",
            "updatedAt",
            "updated_at",
            "orderId",
            "order_id",
            "userId",
            "user_id",
            "status",
            "createdAt",
            "created_at",
            "paidAt",
            "paid_at",
            "totalAmount",
            "total_amount",
            "amount",
            "ageHours",
        }
    }


def _scoped_evidence(evidence: FindingEvidence, entity_key: str) -> FindingEvidence:
    return evidence.model_copy(update={"source_id": f"{evidence.source_id}:{entity_key}"})
