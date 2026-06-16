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


class SuccessfulMcpClient:
    async def get_tools(self, server_name: str) -> list:
        return []

    async def aclose(self) -> None:
        pass


async def test_build_monitor_runtime_closes_mcp_client_on_setup_failure(monkeypatch) -> None:
    client = FailingMcpClient()
    monkeypatch.setattr(system_module, "build_mcp_client", lambda *args, **kwargs: client)

    with pytest.raises(TimeoutError):
        await build_monitor_runtime(Settings(_env_file=None))

    assert client.closed is True


async def test_build_monitor_runtime_skips_cause_agent_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        system_module,
        "build_mcp_client",
        lambda *args, **kwargs: SuccessfulMcpClient(),
    )

    def fail_build_cause_agent(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("cause agent should not be built by default")

    monkeypatch.setattr(system_module, "build_monitor_cause_agent", fail_build_cause_agent)

    runtime = await build_monitor_runtime(
        Settings(_env_file=None, llm_api_key="key", monitor_cause_enabled=False)
    )

    assert runtime.cause_agent is None
