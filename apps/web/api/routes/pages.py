"""Serve the browser application from apps/web/app."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/")
async def index(request: Request):
    return FileResponse(request.app.state.frontend_dir / "chat.html")


@router.get("/connections")
async def connections(request: Request):
    return FileResponse(request.app.state.frontend_dir / "connections.html")
