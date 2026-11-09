"""Conversations and messages. Every query is scoped to the tenant's org and user."""

import math
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import AgentContext, ChatMessage
from app.db.models import Conversation, Message
from app.db.session import session_factory
from app.tenancy.context import TenantContext

RATE_LIMIT_MESSAGES = 20
RATE_LIMIT_WINDOW = timedelta(minutes=1)
TITLE_LENGTH = 60


class RateLimited(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"Retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


async def get_own_conversation(
    db: AsyncSession, tenant: TenantContext, conversation_id: uuid.UUID
) -> Conversation | None:
    """The conversation, if it belongs to this user in this org. Otherwise None, whether it
    doesn't exist or is someone else's, so the response doesn't reveal which."""
    return await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.org_id == tenant.org.id,
            Conversation.user_id == tenant.user.id,
        )
    )


async def enforce_rate_limit(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Allows RATE_LIMIT_MESSAGES user messages per rolling RATE_LIMIT_WINDOW.

    Counted from the messages table, so the limit holds across server processes and restarts.
    The advisory lock makes one user's simultaneous requests check and save one at a time, so
    they can't all pass at once; Postgres releases it when the transaction commits.
    """
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(user_id), 0))))
    sent, oldest, now = (
        await db.execute(
            select(func.count(), func.min(Message.created_at), func.clock_timestamp()).where(
                Message.user_id == user_id,
                Message.role == "user",
                Message.created_at > func.clock_timestamp() - RATE_LIMIT_WINDOW,
            )
        )
    ).one()
    if sent >= RATE_LIMIT_MESSAGES:
        # When the oldest message in the window drops out of it.
        retry_after = math.ceil((oldest + RATE_LIMIT_WINDOW - now).total_seconds())
        raise RateLimited(max(retry_after, 1))


def _title_from(message: str) -> str:
    title = " ".join(message.split())
    return title if len(title) <= TITLE_LENGTH else title[: TITLE_LENGTH - 1].rstrip() + "…"


def create_conversation(db: AsyncSession, tenant: TenantContext, first_message: str) -> Conversation:
    conversation = Conversation(
        id=uuid.uuid4(),
        org_id=tenant.org.id,
        user_id=tenant.user.id,
        title=_title_from(first_message),
    )
    db.add(conversation)
    return conversation


async def add_message(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
) -> Message:
    message = Message(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    db.add(message)
    await db.flush()
    # Keeps the sidebar sorted by latest activity.
    await db.execute(
        update(Conversation).where(Conversation.id == conversation_id).values(updated_at=func.now())
    )
    return message


async def load_history(db: AsyncSession, tenant: TenantContext, conversation_id: uuid.UUID) -> list[ChatMessage]:
    rows = await db.execute(
        select(Message.role, Message.content)
        .where(
            Message.conversation_id == conversation_id,
            Message.org_id == tenant.org.id,
            Message.user_id == tenant.user.id,
        )
        .order_by(Message.created_at, Message.id)
    )
    return [ChatMessage(role=role, content=content) for role, content in rows]


async def save_reply(context: AgentContext, content: str) -> uuid.UUID:
    """Saves the assistant's reply in its own session, since the request's session has
    closed by the time a streamed reply finishes."""
    async with session_factory()() as db:
        message = await add_message(
            db,
            org_id=context.org_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            role="assistant",
            content=content,
        )
        await db.commit()
        return message.id
