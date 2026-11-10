from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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

    # Knowledge-base embeddings always use Mistral. Needed by `python -m app.seed`.
    mistral_api_key: SecretStr | None = None

    # The chat agent's LLM: "mistral", "gemini", or "placeholder" (a canned reply, no API calls,
    # for testing the chat plumbing). LLM_MODEL overrides the provider's default model.
    llm_provider: Literal["mistral", "gemini", "placeholder"] = "mistral"
    llm_model: str | None = None
    # The most tokens one LLM reply may use. A ceiling, not a target: short replies cost the same.
    # Without it the Mistral client stops every reply at 512 tokens, mid-sentence.
    llm_max_tokens: int = Field(default=2048, gt=0)
    gemini_api_key: SecretStr | None = None

    # Browsers accept Secure cookies on http://localhost, so this stays on in development too.
    cookie_secure: bool = True


@lru_cache
def get_settings() -> Settings:
    # The values come from .env and the environment, which type checkers can't see.
    return Settings()  # pyright: ignore[reportCallIssue]
