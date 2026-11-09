import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, HTTPException, status
from fastapi.sse import EventSourceResponse, format_sse_event
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.agent import get_agent
from app.agent.base import Agent, AgentContext, ChatMessage, TextDelta, ToolActivity
from app.db.session import DbSession
from app.services.chat import (
    RATE_LIMIT_MESSAGES,
    RateLimited,
    add_message,
    create_conversation,
    enforce_rate_limit,
    get_own_conversation,
    load_history,
    save_reply,
)
from app.tenancy.context import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    # Unknown fields are refused, so a body can't even attempt to pass an org_id or user_id.
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    # Omit to start a new conversation.
    conversation_id: uuid.UUID | None = None


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return format_sse_event(event=event, data_str=json.dumps(data))


# Returns an EventSourceResponse itself rather than setting response_class, which would make
# FastAPI expect the endpoint to be a generator. This way, the checks before streaming can still
# answer with ordinary HTTP errors.
@router.post(
    "/orgs/{org_slug}/chat",
    responses={200: {"description": "The reply, as Server-Sent Events.", "content": {"text/event-stream": {}}}},
)
async def chat(body: ChatRequest, tenant: Tenant, db: DbSession) -> EventSourceResponse:
    """Sends a message to the org's agent and streams the reply as Server-Sent Events.

    Events, in order:
      conversation  {"id", "title"}        which conversation the message went to
      tool          {"tool", "label"}      the agent is using a tool (zero or more)
      token         {"text"}               the next piece of the reply (zero or more)
      done          {"message_id"}         the reply is complete and saved
      error         {"message"}            the agent failed; any partial reply is saved

    Errors found before streaming starts are ordinary HTTP errors: 401 not signed in,
    403 another org, 404 conversation not found (or not yours), 422 bad body, 429 rate limit.
    """
    conversation = None
    if body.conversation_id is not None:
        conversation = await get_own_conversation(db, tenant, body.conversation_id)
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")

    try:
        await enforce_rate_limit(db, tenant.user.id)
    except RateLimited as error:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You can send {RATE_LIMIT_MESSAGES} messages a minute. "
            f"Try again in {error.retry_after_seconds}s.",
            headers={"Retry-After": str(error.retry_after_seconds)},
        )

    if conversation is None:
        conversation = create_conversation(db, tenant, body.message)
        await db.flush()
    await add_message(
        db,
        org_id=tenant.org.id,
        user_id=tenant.user.id,
        conversation_id=conversation.id,
        role="user",
        content=body.message,
    )
    await db.commit()
    history = await load_history(db, tenant, conversation.id)

    context = AgentContext(
        org_id=tenant.org.id,
        org_slug=tenant.org.slug,
        org_name=tenant.org.name,
        user_id=tenant.user.id,
        conversation_id=conversation.id,
    )
    stream = _stream_reply(get_agent(tenant.org), context, history, conversation.title)
    # X-Accel-Buffering: tells proxies such as nginx not to hold the stream back.
    return EventSourceResponse(stream, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _stream_reply(
    agent: Agent, context: AgentContext, history: list[ChatMessage], title: str
) -> AsyncIterator[bytes]:
    yield _sse("conversation", {"id": str(context.conversation_id), "title": title})

    reply: list[str] = []
    failed = False
    message_id = None
    try:
        async for event in agent.run(context, history):
            if isinstance(event, TextDelta):
                reply.append(event.text)
                yield _sse("token", {"text": event.text})
            elif isinstance(event, ToolActivity):
                yield _sse("tool", {"tool": event.tool, "label": event.label})
    except Exception:
        logger.exception("Agent failed in conversation %s", context.conversation_id)
        failed = True
    finally:

            with anyio.CancelScope(shield=True):
                message_id = await save_reply(context, "".join(reply))

    if failed:
        yield _sse("error", {"message": "Something went wrong. Please try again."})
    else:
        yield _sse("done", {"message_id": str(message_id) if message_id else None})
