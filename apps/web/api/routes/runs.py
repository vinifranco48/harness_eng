"""Stream and cancel provider responses."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..dependencies import ChatService, mutation

router = APIRouter(prefix="/api/runs", dependencies=[Depends(mutation)])


@router.get("/{run_id}/events")
async def stream_run(run_id: str, request: Request, chat: ChatService):
    try:
        chat.get_run(run_id)
    except KeyError:
        raise HTTPException(404, "Esta resposta não foi encontrada. Reabra a conversa.") from None

    async def events():
        revision = -1
        ticks = 0
        while not await request.is_disconnected():
            try:
                run = chat.get_run(run_id)
            except KeyError:
                break
            if run["revision"] != revision or run["state"] != "running":
                revision = run["revision"]
                yield "data: " + json.dumps(run, ensure_ascii=False) + "\n\n"
            if run["state"] != "running":
                break
            ticks += 1
            if ticks % 50 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.post("/{run_id}/cancel")
async def stop_run(run_id: str, chat: ChatService):
    try:
        return {"run": await asyncio.to_thread(chat.cancel_run, run_id)}
    except KeyError:
        raise HTTPException(404, "Esta resposta não foi encontrada.") from None
