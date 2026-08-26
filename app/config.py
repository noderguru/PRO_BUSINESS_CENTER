from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str
    # порожньо -> api.openai.com; інакше передається в SDK як base_url
    openai_base_url: str = ""
    openai_default_model: str = "gpt-4o-mini"
    openai_timeout_seconds: int = 60
    openai_max_retries: int = 2

    database_url: str

    app_env: str = "local"
    log_level: str = "INFO"

    context_max_messages: int = 20
    context_max_input_tokens: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
