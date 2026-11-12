"""The chat sidebar: the signed-in user's conversations in this org.

Every endpoint takes a Tenant, so another org's URL is refused (403), and looks conversations up
with get_own_conversation, so another user's conversation id is answered with 404, exactly like
one that doesn't exist.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.session import DbSession
from app.services.chat import clean_title, get_own_conversation, list_conversations, list_messages
from app.tenancy.context import Tenant

router = APIRouter(tags=["conversations"])


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class RenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _out(conversation) -> ConversationOut:
    return ConversationOut(id=conversation.id, title=conversation.title,
                           created_at=conversation.created_at, updated_at=conversation.updated_at)


async def _own(db, tenant, conversation_id: uuid.UUID):
    conversation = await get_own_conversation(db, tenant, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return conversation


@router.get("/orgs/{org_slug}/conversations", response_model=list[ConversationOut])
async def conversations(tenant: Tenant, db: DbSession) -> list[ConversationOut]:
    """The user's conversations, most recently active first."""
    return [_out(c) for c in await list_conversations(db, tenant)]


@router.get("/orgs/{org_slug}/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def messages(conversation_id: uuid.UUID, tenant: Tenant, db: DbSession) -> list[MessageOut]:
    """A conversation's messages, oldest first."""
    await _own(db, tenant, conversation_id)
    return [MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)
            for m in await list_messages(db, tenant, conversation_id)]


@router.patch("/orgs/{org_slug}/conversations/{conversation_id}", response_model=ConversationOut)
async def rename(conversation_id: uuid.UUID, body: RenameIn, tenant: Tenant, db: DbSession) -> ConversationOut:
    conversation = await _own(db, tenant, conversation_id)
    conversation.title = clean_title(body.title)
    await db.commit()
    await db.refresh(conversation)
    return _out(conversation)


@router.delete("/orgs/{org_slug}/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(conversation_id: uuid.UUID, tenant: Tenant, db: DbSession) -> Response:
    """Deletes the conversation with its messages and pending confirmations. Orders and bookings
    made in it are kept (they just lose the link to the conversation)."""
    conversation = await _own(db, tenant, conversation_id)
    await db.delete(conversation)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
