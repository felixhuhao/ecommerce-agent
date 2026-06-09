import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.mongo import MongoThreadStore

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_mongo_append_and_reload() -> None:
    if not os.environ.get("RUN_MONGO_INTEGRATION"):
        pytest.skip("set RUN_MONGO_INTEGRATION and run a local Mongo to exercise this")

    settings = Settings(_env_file=None)
    store = MongoThreadStore.from_settings(settings)
    session_id = f"itest-{os.getpid()}"

    await store.append(ThreadMessage(session_id=session_id, type="user", content="hi"))
    msgs = await store.list_messages(session_id)

    assert msgs[-1].content == "hi"
    assert msgs[-1].seq >= 1
