from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument

from ecommerce_agent.monitoring.models import Alert, AlertStatus


class MongoAlertStore:
    def __init__(
        self,
        *,
        alerts: Any,
        client: Any | None = None,
        retention_days: int = 90,
    ) -> None:
        self._alerts = alerts
        self._client = client
        self._retention_days = retention_days

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        await self._alerts.create_index("expire_at", expireAfterSeconds=0)
        await self._alerts.create_index("dedupe_key")
        await self._alerts.create_index("status")
        await self._alerts.create_index("created_at")

    async def create(self, alert: Alert) -> Alert:
        doc = alert.model_dump(mode="json")
        doc["expire_at"] = datetime.now(UTC) + timedelta(days=self._retention_days)
        await self._alerts.insert_one(doc)
        return alert

    async def get(self, alert_id: str) -> Alert | None:
        doc = await self._alerts.find_one({"alert_id": alert_id})
        return _alert_from_doc(doc)

    async def list(
        self,
        *,
        status: AlertStatus | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        query: dict[str, Any] = {}
        if status is not None:
            query["status"] = status.value
        cursor = self._alerts.find(query).sort("created_at", -1).limit(limit)
        alerts: list[Alert] = []
        async for doc in cursor:
            alert = _alert_from_doc(doc)
            if alert is not None:
                alerts.append(alert)
        return alerts

    async def latest_for_dedupe_key(self, dedupe_key: str) -> Alert | None:
        doc = await self._alerts.find_one({"dedupe_key": dedupe_key}, sort=[("created_at", -1)])
        return _alert_from_doc(doc)

    async def open_for_dedupe_key(self, dedupe_key: str) -> Alert | None:
        doc = await self._alerts.find_one(
            {"dedupe_key": dedupe_key, "status": AlertStatus.OPEN.value},
            sort=[("created_at", -1)],
        )
        return _alert_from_doc(doc)

    async def acknowledge(self, alert_id: str, *, actor_id: str) -> Alert | None:
        now = datetime.now(UTC).isoformat()
        doc = await self._alerts.find_one_and_update(
            {"alert_id": alert_id},
            {
                "$set": {
                    "status": AlertStatus.ACKNOWLEDGED.value,
                    "updated_at": now,
                    "acknowledged_at": now,
                    "acknowledged_by": actor_id,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _alert_from_doc(doc)

    async def ping(self) -> bool:
        if self._client is None:
            return False
        await self._client.admin.command("ping")
        return True


def _alert_from_doc(doc: dict[str, Any] | None) -> Alert | None:
    if doc is None:
        return None
    return Alert(**{key: value for key, value in doc.items() if key not in ("_id", "expire_at")})
