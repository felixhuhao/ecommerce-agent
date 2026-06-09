from ecommerce_agent.config import Settings


def test_settings_defaults_to_external_spring_mcp() -> None:
    settings = Settings(_env_file=None)

    assert settings.spring_mcp_url == "http://localhost:8080/mcp"
    assert settings.spring_mcp_user_id == "1"
    assert settings.modelscope_mcp_url == ""
    assert settings.python_mcp_url == ""


def test_deepseek_api_key_alias_is_supported(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    settings = Settings(_env_file=None)

    assert settings.llm_api_key == "deepseek-key"


def test_llm_temperature_defaults_low() -> None:
    assert Settings(_env_file=None).llm_temperature == 0.1


def test_agent_recursion_limit_has_explicit_default() -> None:
    assert Settings(_env_file=None).agent_recursion_limit == 80


def test_sandbox_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.sandbox_image == "ecommerce-agent-sandbox:dev"
    assert settings.sandbox_memory == "512m"
    assert settings.sandbox_cpus == 1.0
    assert settings.sandbox_pids == 128
    assert settings.sandbox_execute_timeout_seconds == 30
    assert settings.sandbox_idle_ttl_seconds == 600
