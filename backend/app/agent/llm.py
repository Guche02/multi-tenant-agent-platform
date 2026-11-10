"""The chat LLM, chosen by the LLM_PROVIDER environment variable."""

from functools import lru_cache

from llama_index.core.llms.function_calling import FunctionCallingLLM

from app.config import get_settings

DEFAULT_MODELS = {
    "mistral": "mistral-large-latest",
    "gemini": "gemini-3.7-flash",
}
# Low, so tool use and policy answers stay consistent; not zero, so replies don't sound canned.
TEMPERATURE = 0.2


@lru_cache
def get_llm() -> FunctionCallingLLM:
    settings = get_settings()
    provider = settings.llm_provider
    model = settings.llm_model or DEFAULT_MODELS.get(provider, "")

    if provider == "mistral":
        from llama_index.llms.mistralai import MistralAI

        if settings.mistral_api_key is None:
            raise RuntimeError("LLM_PROVIDER=mistral needs MISTRAL_API_KEY in .env.")
        # The SDK retries rate-limited (429) and failed requests with backoff: the free tier is tight.
        return MistralAI(
            model=model, api_key=settings.mistral_api_key.get_secret_value(), temperature=TEMPERATURE,
            max_tokens=settings.llm_max_tokens, max_retries=5,
        )

    if provider == "gemini":
        from llama_index.llms.google_genai import GoogleGenAI

        if settings.gemini_api_key is None:
            raise RuntimeError("LLM_PROVIDER=gemini needs GEMINI_API_KEY in .env.")
        return GoogleGenAI(
            model=model, api_key=settings.gemini_api_key.get_secret_value(), temperature=TEMPERATURE,
            max_tokens=settings.llm_max_tokens, max_retries=5,
        )

    raise RuntimeError(f"LLM_PROVIDER={provider!r} has no LLM.")
