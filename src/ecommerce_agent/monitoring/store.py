from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ecommerce_agent.monitoring.models import Alert, AlertStatus


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class AlertStore(Protocol):
    async def create(self, alert: Alert) -> Alert:
        ...

    async def get(self, alert_id: str) -> Alert | None:
        ...

    async def list(self, *, status: AlertStatus | None = None, limit: int = 100) -> list[Alert]:
        ...

    async def latest_for_dedupe_key(self, dedupe_key: str) -> Alert | None:
        ...

    async def open_for_dedupe_key(self, dedupe_key: str) -> Alert | None:
        ...

    async def acknowledge(self, alert_id: str, *, actor_id: str) -> Alert | None:
        ...

    async def ping(self) -> bool:
        ...


class InMemoryAlertStore:
    def __init__(
        self,
        *,
        retention_days: int = 90,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._alerts: dict[str, Alert] = {}
        self._lock = asyncio.Lock()
        self._retention_days = retention_days
        self._now = now

    async def create(self, alert: Alert) -> Alert:
        async with self._lock:
            self._alerts[alert.alert_id] = alert
            return alert

    async def get(self, alert_id: str) -> Alert | None:
        async with self._lock:
            return self._alerts.get(alert_id)

    async def list(
        self,
        *,
        status: AlertStatus | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        async with self._lock:
            alerts = sorted(
                self._alerts.values(),
                key=lambda alert: _parse_iso(alert.created_at),
                reverse=True,
            )
            if status is not None:
                alerts = [alert for alert in alerts if alert.status == status]
            return alerts[:limit]

    async def latest_for_dedupe_key(self, dedupe_key: str) -> Alert | None:
        async with self._lock:
            matches = [
                alert for alert in self._alerts.values() if alert.dedupe_key == dedupe_key
            ]
            if not matches:
                return None
            return max(matches, key=lambda alert: _parse_iso(alert.created_at))

    async def open_for_dedupe_key(self, dedupe_key: str) -> Alert | None:
        async with self._lock:
            matches = [
                alert
                for alert in self._alerts.values()
                if alert.dedupe_key == dedupe_key and alert.status == AlertStatus.OPEN
            ]
            if not matches:
                return None
            return max(matches, key=lambda alert: _parse_iso(alert.created_at))

    async def acknowledge(self, alert_id: str, *, actor_id: str) -> Alert | None:
        async with self._lock:
            alert = self._alerts.get(alert_id)
            if alert is None:
                return None
            now = self._now().isoformat()
            updated = alert.model_copy(
                update={
                    "status": AlertStatus.ACKNOWLEDGED,
                    "updated_at": now,
                    "acknowledged_at": now,
                    "acknowledged_by": actor_id,
                }
            )
            self._alerts[alert_id] = updated
            return updated

    async def ping(self) -> bool:
        return True

    async def sweep_expired(self) -> int:
        cutoff = self._now() - timedelta(days=self._retention_days)
        removed = 0
        async with self._lock:
            for alert_id, alert in list(self._alerts.items()):
                if _parse_iso(alert.created_at) < cutoff:
                    self._alerts.pop(alert_id, None)
                    removed += 1
        return removed

