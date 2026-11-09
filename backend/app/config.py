from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The repo root, where .env lives.
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str

    supabase_url: str
    # Safe to expose: only allows what Supabase lets anonymous clients do.
    supabase_publishable_key: SecretStr
    # Full admin access to Supabase. Server only, never sent to a browser.
    supabase_secret_key: SecretStr

    # Embeddings for the knowledge base (and, later, the LLM). Needed by `python -m app.seed`.
    mistral_api_key: SecretStr | None = None

    # Browsers accept Secure cookies on http://localhost, so this stays on in development too.
    cookie_secure: bool = True


@lru_cache
def get_settings() -> Settings:
    # The values come from .env and the environment, which type checkers can't see.
    return Settings()  # pyright: ignore[reportCallIssue]
