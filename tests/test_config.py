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


def test_sandbox_backend_defaults_to_docker() -> None:
    settings = Settings(_env_file=None)

    assert settings.sandbox_backend == "docker"
    assert settings.sandbox_executor_url == ""
    assert settings.sandbox_executor_token == ""


def test_settings_expose_m2_session_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.mongo_url == "mongodb://localhost:27017"
    assert settings.mongo_db == "ecommerce_agent"
    assert settings.approval_api_base_url == "http://localhost:8080"
    assert settings.session_idle_ttl_seconds == 1800
    assert settings.max_live_sessions == 50


def test_settings_expose_frontend_dist_dir() -> None:
    assert Settings(_env_file=None).frontend_dist_dir == "frontend/dist"
    assert Settings(_env_file=None, frontend_dist_dir="/tmp/x").frontend_dist_dir == "/tmp/x"


def test_auth_and_audit_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.auth_cookie_name == "ea_session"
    assert settings.auth_cookie_secure is False
    assert settings.auth_session_ttl_seconds == 28800
    assert settings.audit_retention_days == 90


def test_grounding_evidence_default() -> None:
    assert Settings(_env_file=None).grounding_evidence_max_chars == 2000


def test_monitoring_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.monitor_enabled is False
    assert settings.monitor_interval_seconds == 900
    assert settings.monitor_low_stock_threshold == 50
    assert settings.monitor_sales_drop_pct == 0.25
    assert settings.monitor_cooldown_seconds == 86400
    assert settings.monitor_cause_enabled is False
    assert settings.alert_retention_days == 90
    assert settings.monitor_spring_user_id == "1"
    assert settings.monitor_spring_session_id == "monitor"
