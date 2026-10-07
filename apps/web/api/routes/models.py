"""Discover models available to the connected provider accounts."""

import asyncio

from fastapi import APIRouter, Depends

from ..dependencies import ChatService, mutation
from ..domain.auth import Provider

router = APIRouter(prefix="/api/models", dependencies=[Depends(mutation)])


@router.get("")
async def models(chat: ChatService, refresh: bool = False):
    catalogs = await asyncio.gather(
        *(asyncio.to_thread(chat.catalog.list, provider, refresh) for provider in Provider)
    )
    return {"providers": catalogs}
