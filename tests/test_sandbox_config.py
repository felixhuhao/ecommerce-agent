from ecommerce_agent.config import Settings
from ecommerce_agent.sandbox.config import (
    SANDBOX_LABEL,
    SandboxLimits,
    container_run_kwargs,
    limits_from_settings,
)


def test_limits_from_settings_converts_cpus_to_nano() -> None:
    limits = limits_from_settings(Settings(_env_file=None))
    assert limits.image == "ecommerce-agent-sandbox:dev"
    assert limits.nano_cpus == 1_000_000_000
    assert limits.pids_limit == 128
    assert limits.execute_timeout_seconds == 30


def test_container_run_kwargs_is_hardened() -> None:
    limits = SandboxLimits(
        image="img:dev",
        mem_limit="256m",
        nano_cpus=500_000_000,
        pids_limit=64,
        execute_timeout_seconds=10,
        idle_ttl_seconds=300,
    )
    kwargs = container_run_kwargs(limits, name="ecommerce-sandbox-abc")

    assert kwargs["image"] == "img:dev"
    assert kwargs["name"] == "ecommerce-sandbox-abc"
    assert kwargs["command"] == ["sleep", "infinity"]
    assert kwargs["detach"] is True
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["user"] == "sandbox"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["nano_cpus"] == 500_000_000
    assert kwargs["pids_limit"] == 64
    assert "/workspace" in kwargs["tmpfs"]
    assert "/tmp" in kwargs["tmpfs"]
    assert kwargs["working_dir"] == "/workspace"


def test_container_run_kwargs_labels_runtime_sandbox() -> None:
    limits = SandboxLimits(
        image="img:dev",
        mem_limit="256m",
        nano_cpus=500_000_000,
        pids_limit=64,
        execute_timeout_seconds=10,
        idle_ttl_seconds=300,
    )
    kwargs = container_run_kwargs(limits, name="ecommerce-sandbox-abc")

    assert kwargs["labels"][SANDBOX_LABEL] == "true"
