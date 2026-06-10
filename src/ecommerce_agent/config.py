from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "ecommerce-agent"
    environment: str = "local"

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "DEEPSEEK_API_KEY"),
    )
    llm_model: str = "deepseek-chat"
    llm_temperature: float = Field(default=0.1, ge=0)
    agent_recursion_limit: int = Field(default=80, gt=0)

    spring_mcp_url: str = "http://localhost:8080/mcp"
    spring_mcp_service_token: str = "dev-service-token"
    spring_mcp_user_id: str = "1"
    spring_mcp_session_id: str = "local-session"

    modelscope_mcp_url: str = ""
    python_mcp_url: str = ""

    mcp_request_timeout_seconds: float = Field(default=30.0, gt=0)
    mcp_sse_read_timeout_seconds: float = Field(default=300.0, gt=0)

    # Sandbox (DockerSandbox backend)
    sandbox_image: str = "ecommerce-agent-sandbox:dev"
    sandbox_memory: str = "512m"
    sandbox_cpus: float = Field(default=1.0, gt=0)
    sandbox_pids: int = Field(default=128, gt=0)
    sandbox_execute_timeout_seconds: int = Field(default=30, gt=0)
    sandbox_idle_ttl_seconds: int = Field(default=600, gt=0)

    # M2 session / conversation thread
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "ecommerce_agent"
    approval_api_base_url: str = "http://localhost:8080"
    session_idle_ttl_seconds: int = Field(default=1800, gt=0)
    max_live_sessions: int = Field(default=50, gt=0)
    frontend_dist_dir: str = "frontend/dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()
