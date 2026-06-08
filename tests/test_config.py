from ecommerce_agent.config import Settings


def test_settings_defaults_to_external_spring_mcp() -> None:
    settings = Settings()

    assert settings.spring_mcp_url == "http://localhost:8080/mcp"
    assert settings.spring_mcp_user_id == "1"
    assert settings.modelscope_mcp_url == ""
    assert settings.python_mcp_url == ""
