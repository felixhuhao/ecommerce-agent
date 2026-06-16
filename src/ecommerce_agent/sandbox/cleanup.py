"""Stale sandbox-container cleanup helpers.

Targets only agent-owned sandbox containers (by the `com.ecommerce-agent.sandbox` label, or the
`ecommerce-sandbox-` name prefix for legacy unlabeled containers) so cleanup never touches
unrelated containers like Mongo or chart services.
"""

from __future__ import annotations

from typing import Any, Protocol

from ecommerce_agent.sandbox.config import SANDBOX_LABEL, SANDBOX_NAME_PREFIX


class _ContainerLike(Protocol):
    name: str
    labels: dict[str, str]

    def remove(self, *, force: bool = ...) -> None: ...


class _DockerClientLike(Protocol):
    containers: Any


def is_sandbox_container(container: _ContainerLike) -> bool:
    labels = getattr(container, "labels", None) or {}
    if labels.get(SANDBOX_LABEL) == "true":
        return True
    name = getattr(container, "name", "") or ""
    return name.startswith(SANDBOX_NAME_PREFIX)


def list_sandbox_containers(
    client: _DockerClientLike, *, all_states: bool = True
) -> list[_ContainerLike]:
    listed = client.containers.list(all=all_states)
    return [c for c in listed if is_sandbox_container(c)]


def cleanup_sandbox_containers(client: _DockerClientLike) -> list[str]:
    removed: list[str] = []
    for container in list_sandbox_containers(client, all_states=True):
        container.remove(force=True)
        removed.append(container.name)
    return removed


def main() -> int:
    import docker

    client = docker.from_env()
    removed = cleanup_sandbox_containers(client)
    if removed:
        print(f"removed {len(removed)} sandbox container(s):")
        for name in removed:
            print(f"  - {name}")
    else:
        print("no stale sandbox containers found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
