"""Conversation history and message submission."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import ChatService, mutation
from ..domain.errors import ChatConflict, HarnessError
from .schemas import ChatRequest, MessageRequest

router = APIRouter(prefix="/api/chats", dependencies=[Depends(mutation)])


@router.get("")
async def chats(chat: ChatService):
    return {"chats": await asyncio.to_thread(chat.store.list)}


@router.post("", status_code=201)
async def new_chat(body: ChatRequest, chat: ChatService):
    try:
        return {"chat": await asyncio.to_thread(chat.create, body.provider, body.model, body.effort)}
    except HarnessError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/{chat_id}")
async def get_chat(chat_id: str, chat: ChatService):
    try:
        return {"chat": await asyncio.to_thread(chat.public_chat, chat_id)}
    except KeyError:
        raise HTTPException(404, "Esta conversa não foi encontrada.") from None


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str, chat: ChatService):
    try:
        await asyncio.to_thread(chat.delete, chat_id)
        return {"deleted": True}
    except KeyError:
        raise HTTPException(404, "Esta conversa não foi encontrada.") from None
    except ChatConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/{chat_id}/messages", status_code=202)
async def send_message(chat_id: str, body: MessageRequest, chat: ChatService):
    try:
        run = await asyncio.to_thread(chat.send, chat_id, body.text, body.model, body.effort)
        return {"run": run, "chat": await asyncio.to_thread(chat.public_chat, chat_id)}
    except KeyError:
        raise HTTPException(404, "Esta conversa não foi encontrada.") from None
    except ChatConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except HarnessError as exc:
        raise HTTPException(400, str(exc)) from None
