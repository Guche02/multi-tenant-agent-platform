"""Pieces shared by every toolset.

Every tool is a closure over the AgentContext, which the server builds from the URL and the
session. Tools never take an org or customer id as an argument, so nothing the customer types,
and nothing the model invents, can point a tool at another org's or another customer's data.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from llama_index.core.schema import MetadataMode
from llama_index.core.tools import FunctionTool
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import AgentContext
from app.db.models import Message, PendingAction
from app.retrieval.retriever import search_knowledge

# What the model is told when a tool has recorded a request and needs the customer's yes.
CONFIRM_NEXT_STEP = (
    "Nothing has been done yet. Show the customer this summary and ask them to confirm. "
    "If they confirm in their next message, call this tool again with exactly the same arguments "
    "to carry it out. If they change anything, call it with the new details to get a new summary."
)


def result(**data: Any) -> str:
    """Tools return JSON text: the model reads it, and the chat stream forwards it to the UI."""
    return json.dumps(data, default=str, ensure_ascii=False)


def make_tool(fn: Callable[..., Awaitable[str]], name: str, description: str) -> FunctionTool:
    return FunctionTool.from_defaults(async_fn=fn, name=name, description=description)


async def confirmation_given(db: AsyncSession, ctx: AgentContext, action: str, args: dict[str, Any]) -> bool:
    """Whether the customer has confirmed this exact request.

    True only if the same request (same action, same normalised arguments) was recorded during
    the agent's previous turn, i.e. after the customer's previous message and before their
    latest one. Otherwise the request is recorded now, to be confirmed on their next message.
    So the model can't carry out a request in the same turn it first shows it, and a "yes"
    only counts for the summary the customer has just seen.
    """
    args_hash = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()
    user_turns = (
        await db.scalars(
            select(Message.created_at)
            .where(
                Message.conversation_id == ctx.conversation_id,
                Message.org_id == ctx.org_id,
                Message.user_id == ctx.user_id,
                Message.role == "user",
            )
            .order_by(Message.created_at.desc())
            .limit(2)
        )
    ).all()
    latest = user_turns[0] if user_turns else None
    previous = user_turns[1] if len(user_turns) > 1 else None

    pending = await db.scalar(
        select(PendingAction).where(
            PendingAction.conversation_id == ctx.conversation_id,
            PendingAction.org_id == ctx.org_id,
            PendingAction.user_id == ctx.user_id,
            PendingAction.action == action,
            PendingAction.args_hash == args_hash,
        )
    )
    if (
        pending is not None
        and latest is not None
        and pending.created_at < latest
        and (previous is None or pending.created_at > previous)
    ):
        await db.delete(pending)
        return True

    stmt = insert(PendingAction).values(
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        action=action,
        args_hash=args_hash,
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["conversation_id", "action", "args_hash"],
            set_={"created_at": func.clock_timestamp()},
        )
    )
    return False


def knowledge_tool(ctx: AgentContext) -> FunctionTool:
    async def search_knowledge_base(
        query: Annotated[str, Field(description="What to look up, as a short question or keywords.")],
    ) -> str:
        hits = await search_knowledge(ctx.org_id, query, top_k=5)
        return result(
            results=[
                {
                    "document": hit.node.metadata["document_title"],
                    "section": hit.node.metadata["section"],
                    "text": hit.node.get_content(metadata_mode=MetadataMode.NONE),
                }
                for hit in hits
            ]
        )

    return make_tool(
        search_knowledge_base,
        "search_knowledge",
        f"Searches {ctx.org_name}'s knowledge base (policies, FAQs, descriptions) and returns the "
        "most relevant sections, each with its document title and section name for citing. "
        "Use it before answering any question about the business. It never contains live prices, "
        "stock, availability or opening hours.",
    )
