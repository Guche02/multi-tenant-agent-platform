"""The agent: a LlamaIndex FunctionAgent, built per request from the org's config.

The loop, step by step (FunctionAgent, llama_index.core.agent.workflow):
  1. Send the LLM the system prompt, the conversation so far and the tools' schemas.
  2. Stream the LLM's reply. If it asks for tool calls, run them (our tools, bound to this org
     and customer) and add their results to the conversation.
  3. Repeat from 1 until the LLM replies without asking for a tool, or MAX_ITERATIONS is hit.
Each streamed piece of text and each tool call/result is passed to the chat endpoint as an event.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from llama_index.core.agent.workflow import AgentStream, FunctionAgent, ToolCall, ToolCallResult
from llama_index.core.agent.workflow.workflow_events import AgentWorkflowStartEvent
from llama_index.core.llms import ChatMessage as LlamaChatMessage
from llama_index.core.llms import MessageRole

from app.agent.base import AgentContext, AgentEvent, ChatMessage, TextDelta, ToolActivity, ToolResult
from app.agent.llm import get_llm
from app.agent.prompting import render_system_prompt
from app.agent.tools import bookings as booking_tools
from app.agent.tools import orders as order_tools

logger = logging.getLogger(__name__)

TOOLSETS = {"orders": order_tools, "bookings": booking_tools}
# Earlier messages beyond this are left out, to keep requests small on the free tier.
HISTORY_MESSAGES = 20
MAX_ITERATIONS = 12


def _to_llama(message: ChatMessage) -> LlamaChatMessage:
    role = MessageRole.USER if message.role == "user" else MessageRole.ASSISTANT
    return LlamaChatMessage(role=role, content=message.content)


class LlamaIndexAgent:
    async def run(self, context: AgentContext, history: list[ChatMessage]) -> AsyncIterator[AgentEvent]:
        toolset = TOOLSETS[context.config.agent.toolset]
        tools = await toolset.build_tools(context)
        agent = FunctionAgent(
            name=f"{context.org_slug}-assistant",
            description=f"Customer assistant for {context.org_name}",
            system_prompt=render_system_prompt(context, tools),
            tools=tools,
            llm=get_llm(),
        )
        # Empty replies (a stopped or failed turn) are skipped: providers reject empty messages.
        earlier = [m for m in history[:-1] if m.content.strip()][-HISTORY_MESSAGES:]
        handler = agent.run(
            start_event=AgentWorkflowStartEvent(
                user_msg=history[-1].content,
                chat_history=[_to_llama(m) for m in earlier],
                max_iterations=MAX_ITERATIONS,
            )
        )
        try:
            async for event in handler.stream_events():
                if isinstance(event, AgentStream):
                    text = _reply_text(event)
                    if text:
                        yield TextDelta(text)
                elif isinstance(event, ToolCallResult):
                    yield ToolResult(event.tool_name, _parse(event.tool_output.content))
                elif isinstance(event, ToolCall):
                    label = toolset.TOOL_LABELS.get(event.tool_name, "Working…")
                    yield ToolActivity(event.tool_name, label)
            await handler
        finally:
            # The customer pressed Stop, or something failed: don't leave the agent running.
            if not handler.done():
                await handler.cancel_run()


def _reply_text(event: AgentStream) -> str:
    """The reply text in one streamed piece, without the model's reasoning.

    Mistral's models stream their reasoning as "thinking" chunks next to the reply's "text" chunks.
    The LlamaIndex Mistral integration (0.11) adds the thinking to `delta` too, one line per chunk
    (it only separates it for the models on its own reasoning list), so the customer would see it.
    For Mistral, the text is taken from `raw` instead, keeping only the reply. `raw` is either the
    whole stream event (when the content is a plain string) or, because the integration's loop
    reuses the name `chunk`, the last content chunk itself: {"type": "thinking" | "text", ...}.
    Other providers use `delta` as it is.
    """
    raw = event.raw if isinstance(event.raw, dict) else {}
    if raw.get("type") == "thinking":
        return ""
    if raw.get("type") == "text":
        return raw.get("text") or ""
    choices = (raw.get("data") or {}).get("choices") or []
    if not choices:
        return event.delta
    content = (choices[0].get("delta") or {}).get("content")
    if not content:
        return ""
    if isinstance(content, str):
        return content
    return "".join(c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text")


def _parse(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return {"text": content}
    return data if isinstance(data, dict) else {"result": data}
