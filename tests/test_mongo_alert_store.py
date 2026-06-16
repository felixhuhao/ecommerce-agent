from ecommerce_agent.grounding.model import Authority
from ecommerce_agent.monitoring.models import Alert, AlertGrounding, AlertStatus
from ecommerce_agent.monitoring.mongo import MongoAlertStore


class FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def sort(self, key: str, direction: int):  # noqa: ANN001
        reverse = direction < 0
        self.docs = sorted(self.docs, key=lambda doc: doc[key], reverse=reverse)
        return self

    def limit(self, limit: int):
        self.docs = self.docs[:limit]
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.docs:
            raise StopAsyncIteration
        return self.docs.pop(0)


class FakeAlerts:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []

    async def create_index(self, *args, **kwargs) -> None:
        self.indexes.append((args, kwargs))

    async def insert_one(self, doc: dict) -> None:
        self.docs.append(dict(doc))

    async def find_one(self, query: dict, sort: list[tuple] | None = None) -> dict | None:
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            key, direction = sort[0]
            matches = sorted(matches, key=lambda doc: doc[key], reverse=direction < 0)
        return matches[0] if matches else None

    def find(self, query: dict) -> FakeCursor:
        return FakeCursor([doc for doc in self.docs if _matches(doc, query)])

    async def find_one_and_update(self, query: dict, update: dict, return_document) -> dict | None:
        doc = await self.find_one(query)
        if doc is None:
            return None
        doc.update(update["$set"])
        return doc


def _matches(doc: dict, query: dict) -> bool:
    return all(doc.get(key) == value for key, value in query.items())


def alert() -> Alert:
    return Alert(
        alert_id="a1",
        check_name="low_stock",
        dedupe_key="low_stock:SKU-9",
        title="Low stock: SKU-9",
        metric="inventory",
        grounding=AlertGrounding(authority=Authority.AUTHORITATIVE),
    )


async def test_mongo_alert_store_create_get_list_and_ttl() -> None:
    collection = FakeAlerts()
    store = MongoAlertStore(alerts=collection, retention_days=7)

    await store.ensure_indexes()
    await store.create(alert())

    stored_doc = collection.docs[0]
    assert "expire_at" in stored_doc
    assert any(
        args == ("expire_at",) and kwargs == {"expireAfterSeconds": 0}
        for args, kwargs in collection.indexes
    )
    assert (await store.get("a1")).title == "Low stock: SKU-9"  # type: ignore[union-attr]
    assert [item.alert_id for item in await store.list(status=AlertStatus.OPEN)] == ["a1"]
    assert (await store.open_for_dedupe_key("low_stock:SKU-9")).alert_id == "a1"  # type: ignore[union-attr]
    assert (await store.latest_for_dedupe_key("low_stock:SKU-9")).alert_id == "a1"  # type: ignore[union-attr]


async def test_mongo_alert_store_acknowledges_alert() -> None:
    collection = FakeAlerts()
    store = MongoAlertStore(alerts=collection)
    await store.create(alert())

    acknowledged = await store.acknowledge("a1", actor_id="op1")

    assert acknowledged is not None
    assert acknowledged.status == AlertStatus.ACKNOWLEDGED
    assert acknowledged.acknowledged_by == "op1"
    assert acknowledged.acknowledged_at is not None
