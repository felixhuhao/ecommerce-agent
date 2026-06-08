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
