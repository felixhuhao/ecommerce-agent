from ecommerce_agent.sandbox.cleanup import cleanup_sandbox_containers, list_sandbox_containers
from ecommerce_agent.sandbox.config import SANDBOX_LABEL


class FakeContainer:
    def __init__(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.name = name
        self.labels = labels or {}
        self.removed = False
        self.removed_force: bool | None = None

    def remove(self, *, force: bool = False) -> None:
        self.removed = True
        self.removed_force = force


class FakeDockerClient:
    def __init__(self, containers: list[FakeContainer]) -> None:
        self.containers = _FakeContainerCollection(containers)


class _FakeContainerCollection:
    def __init__(self, items: list[FakeContainer]) -> None:
        self._items = list(items)

    def list(self, all: bool = False) -> list[FakeContainer]:  # noqa: A002
        return list(self._items)


def _mixed_containers() -> list[FakeContainer]:
    return [
        FakeContainer("ecommerce-sandbox-aaa", {SANDBOX_LABEL: "true"}),
        FakeContainer("ecommerce-sandbox-legacy", {}),
        FakeContainer("ecommerce-agent-mongo", {}),
        FakeContainer("some-other-container", {"foo": "bar"}),
        FakeContainer("impostor", {SANDBOX_LABEL: "false"}),
    ]


def test_list_sandbox_containers_selects_labeled_and_prefixed_only() -> None:
    client = FakeDockerClient(_mixed_containers())

    selected = list_sandbox_containers(client)

    names = {c.name for c in selected}
    assert names == {"ecommerce-sandbox-aaa", "ecommerce-sandbox-legacy"}


def test_cleanup_removes_only_sandbox_containers_with_force() -> None:
    containers = _mixed_containers()
    client = FakeDockerClient(containers)

    removed = cleanup_sandbox_containers(client)

    assert set(removed) == {"ecommerce-sandbox-aaa", "ecommerce-sandbox-legacy"}
    sandbox_aaa = next(c for c in containers if c.name == "ecommerce-sandbox-aaa")
    sandbox_legacy = next(c for c in containers if c.name == "ecommerce-sandbox-legacy")
    assert sandbox_aaa.removed and sandbox_aaa.removed_force is True
    assert sandbox_legacy.removed and sandbox_legacy.removed_force is True
    for other in containers:
        if other.name not in {"ecommerce-sandbox-aaa", "ecommerce-sandbox-legacy"}:
            assert other.removed is False


def test_list_sandbox_containers_passes_all_state_flag() -> None:
    seen_all_values: list[bool] = []
    containers = _mixed_containers()

    class _CapturingCollection(_FakeContainerCollection):
        def list(self, all: bool = False) -> list[FakeContainer]:  # noqa: A002
            seen_all_values.append(all)
            return super().list(all=all)

    client = FakeDockerClient.__new__(FakeDockerClient)
    client.containers = _CapturingCollection(containers)

    list_sandbox_containers(client)

    assert seen_all_values == [True]
