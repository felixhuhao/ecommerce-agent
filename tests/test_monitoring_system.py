import pytest

import ecommerce_agent.monitoring.system as system_module
from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.system import build_monitor_runtime


class FailingMcpClient:
    def __init__(self) -> None:
        self.closed = False

    async def get_tools(self, server_name: str) -> list:
        raise TimeoutError(f"{server_name} unavailable")

    async def aclose(self) -> None:
        self.closed = True


async def test_build_monitor_runtime_closes_mcp_client_on_setup_failure(monkeypatch) -> None:
    client = FailingMcpClient()
    monkeypatch.setattr(system_module, "build_mcp_client", lambda *args, **kwargs: client)

    with pytest.raises(TimeoutError):
        await build_monitor_runtime(Settings(_env_file=None))

    assert client.closed is True

