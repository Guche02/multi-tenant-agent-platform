from app.agent.base import Agent
from app.config import get_settings
from app.db.models import Organization


def get_agent(org: Organization) -> Agent:
    """The org's agent. Every org gets the same LlamaIndex agent class; what it knows and can do
    comes from the org's config (its toolset, business and policies), filled into one template.

    LLM_PROVIDER=placeholder gives a canned reply with no LLM calls, for testing the chat plumbing.
    """
    if get_settings().llm_provider == "placeholder":
        from app.agent.placeholder import PlaceholderAgent

        return PlaceholderAgent()
    from app.agent.llamaindex_agent import LlamaIndexAgent

    return LlamaIndexAgent()
