from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./purchasing.db"
    checkpoint_db: str = "./checkpoints.db"
    demo_auth_enabled: bool = True
    llm_enabled: bool = True
    llm_provider: str = "gemini"
    llm_fallback_providers: str = "groq,nvidia"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "groq/compound"
    # Compound is retained for explanations. Custom application tools require
    # a separate tool-capable model.
    groq_agent_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = Field(default=30, ge=10)
    max_recovery_attempts: int = 2
    # Mock source timestamps are static between application restarts. Keep the
    # local demo usable during a live review without changing the stale-forecast
    # scenario, which is still governed by its own 24-hour policy.
    demo_volatile_freshness_minutes: int = 60
    frontend_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
