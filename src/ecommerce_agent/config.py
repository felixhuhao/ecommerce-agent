from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ecommerce-agent"
    environment: str = "local"

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    spring_mcp_url: str = "http://localhost:8080/mcp"
    spring_mcp_service_token: str = "dev-service-token"
    spring_mcp_user_id: str = "1"
    spring_mcp_session_id: str = "local-session"

    modelscope_mcp_url: str = ""
    python_mcp_url: str = ""

    mcp_request_timeout_seconds: float = Field(default=30.0, gt=0)
    mcp_sse_read_timeout_seconds: float = Field(default=300.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
