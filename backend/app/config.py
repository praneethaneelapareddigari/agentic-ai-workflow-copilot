"""
Centralized application configuration.

All runtime configuration is pulled from environment variables (with sane
defaults for local development) so the same image can be promoted from
dev -> staging -> prod without code changes (12-factor style config).
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App ---
    APP_NAME: str = "Agentic AI Workflow Copilot"
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    API_PREFIX: str = "/api/v1"

    # --- Database ---
    # Defaults to a local SQLite file so the project runs with zero external
    # infrastructure. Point DATABASE_URL at Postgres in staging/prod, e.g.:
    #   postgresql+asyncpg://user:pass@postgres:5432/agentic_copilot
    DATABASE_URL: str = "sqlite+aiosqlite:///./agentic_copilot.db"

    # --- Redis / caching ---
    # If unset (or unreachable), the app transparently falls back to an
    # in-process LRU cache so local dev / CI never hard-depend on Redis.
    REDIS_URL: Optional[str] = "redis://localhost:6379/0"
    CACHE_TTL_SECONDS: int = 60 * 30  # 30 minutes
    CACHE_NAMESPACE: str = "agentic_copilot"

    # --- LLM provider ---
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT_SECONDS: int = 30
    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BASE_DELAY: float = 0.5
    # When true (default if no API key is configured), a deterministic
    # MockLLMClient stands in for OpenAI so the whole pipeline is fully
    # demoable / testable offline and in CI without network access or a key.
    USE_MOCK_LLM: bool = True

    # --- Pipeline ---
    MAX_PLAN_STEPS: int = 8
    MAX_TOOL_RETRIES: int = 2
    PIPELINE_STEP_TIMEOUT_SECONDS: int = 20

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
