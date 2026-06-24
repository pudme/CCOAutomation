from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ai.agent import run_agent
from database import get_db
from models.compliance import Conversation, Message

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatStreamRequest(BaseModel):
    message: str
    conversation_id: int | None = None


@router.post("/conversations")
async def create_conversation(session: AsyncSession = Depends(get_db)) -> dict[str, int]:
    conversation = Conversation(
        title="New Conversation",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return {"conversation_id": conversation.id}


@router.get("/conversations")
async def list_conversations(session: AsyncSession = Depends(get_db)) -> list[dict[str, str | int | None]]:
    result = await session.execute(select(Conversation).order_by(desc(Conversation.updated_at)))
    conversations = list(result.scalars())
    response: list[dict[str, str | int | None]] = []
    for conversation in conversations:
        message_result = await session.execute(
            select(Message.content)
            .where(Message.conversation_id == conversation.id)
            .order_by(desc(Message.id))
            .limit(1)
        )
        preview = message_result.scalar_one_or_none()
        response.append(
            {
                "conversation_id": conversation.id,
                "title": conversation.title,
                "last_message_preview": (preview[:120] if preview else None),
            }
        )
    return response


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int, session: AsyncSession = Depends(get_db)
) -> list[dict[str, str | int | None]]:
    result = await session.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    messages = list(result.scalars())
    return [
        {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "tool_calls": json.dumps(message.tool_calls) if message.tool_calls else None,
        }
        for message in messages
    ]


@router.post("/stream")
async def stream_chat(
    payload: ChatStreamRequest,
    session: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    conversation_id = payload.conversation_id
    if conversation_id is None:
        conversation = Conversation(
            title=payload.message[:60],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        conversation_id = conversation.id

    async def event_generator():
        yield {"event": "conversation", "data": json.dumps({"conversation_id": conversation_id})}
        async for event in run_agent(payload.message, conversation_id, session):
            yield {"event": event["type"], "data": json.dumps(event)}

    return EventSourceResponse(event_generator())

