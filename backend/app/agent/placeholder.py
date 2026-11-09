import asyncio
from collections.abc import AsyncIterator

from app.agent.base import AgentContext, AgentEvent, ChatMessage, TextDelta, ToolActivity


class PlaceholderAgent:
    """Stands in until the real agents exist, so the chat endpoint can be built and tested.

    Streams a fixed reply word by word, like an LLM would.
    """

    WORD_DELAY_SECONDS = 0.05

    async def run(self, context: AgentContext, history: list[ChatMessage]) -> AsyncIterator[AgentEvent]:
        yield ToolActivity(tool="placeholder", label="Thinking…")
        last_message = history[-1].content if history else ""
        reply = (
            f"Hello from the {context.org_name} assistant. The real agent isn't connected yet, "
            f"so this is a placeholder reply. This conversation has {len(history)} message(s). "
            f'You said: "{last_message}"'
        )
        for i, word in enumerate(reply.split(" ")):
            await asyncio.sleep(self.WORD_DELAY_SECONDS)
            yield TextDelta(word if i == 0 else f" {word}")
