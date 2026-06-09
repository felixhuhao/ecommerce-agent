from __future__ import annotations

from dataclasses import dataclass

from ecommerce_agent.config import Settings


@dataclass(frozen=True)
class SandboxLimits:
    image: str
    mem_limit: str
    nano_cpus: int
    pids_limit: int
    execute_timeout_seconds: int
    idle_ttl_seconds: int
    workspace_size: str = "64m"
    tmp_size: str = "32m"


def limits_from_settings(settings: Settings) -> SandboxLimits:
    return SandboxLimits(
        image=settings.sandbox_image,
        mem_limit=settings.sandbox_memory,
        nano_cpus=int(settings.sandbox_cpus * 1_000_000_000),
        pids_limit=settings.sandbox_pids,
        execute_timeout_seconds=settings.sandbox_execute_timeout_seconds,
        idle_ttl_seconds=settings.sandbox_idle_ttl_seconds,
    )


def container_run_kwargs(limits: SandboxLimits, name: str) -> dict:
    """Docker SDK kwargs for a hardened, network-isolated sandbox container."""
    return {
        "image": limits.image,
        "name": name,
        "command": ["sleep", "infinity"],
        "detach": True,
        "working_dir": "/workspace",
        "network_mode": "none",
        "read_only": True,
        "tmpfs": {
            "/workspace": f"rw,size={limits.workspace_size},mode=1777",
            "/tmp": f"rw,size={limits.tmp_size},mode=1777",
        },
        "user": "sandbox",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "mem_limit": limits.mem_limit,
        "nano_cpus": limits.nano_cpus,
        "pids_limit": limits.pids_limit,
        "auto_remove": False,
    }
