"""What the chat endpoint needs from an agent, independent of the LLM provider and tools."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class AgentContext:
    """Who the agent is acting for. Built by the server from the URL and the session cookie.

    Tools must take the org and user from here, never from the model's arguments, so nothing
    the user types (or the model invents) can make a tool read or change another org's data.
    """

    org_id: uuid.UUID
    org_slug: str
    org_name: str
    user_id: uuid.UUID
    conversation_id: uuid.UUID


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class TextDelta:
    """The next piece of the reply text."""

    text: str


@dataclass(frozen=True)
class ToolActivity:
    """A tool call the user should see happening, e.g. "Checking stock…"."""

    tool: str
    label: str


AgentEvent = TextDelta | ToolActivity


class Agent(Protocol):
    def run(self, context: AgentContext, history: list[ChatMessage]) -> AsyncIterator[AgentEvent]:
        """Streams the reply to the last message in `history` (oldest message first)."""
        ...
