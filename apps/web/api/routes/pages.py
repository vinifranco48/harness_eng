"""Serve the browser application from apps/web/app."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from uuid import UUID

router = APIRouter()


@router.get("/")
async def index(request: Request):
    return FileResponse(request.app.state.frontend_dir / "chat.html")


@router.get("/connections")
async def connections(request: Request):
    return FileResponse(request.app.state.frontend_dir / "connections.html")


@router.get("/analyses")
async def analyses(request: Request):
    return FileResponse(request.app.state.frontend_dir / "analyses.html")


@router.get("/analyses/{analysis_id}")
async def analysis_detail(analysis_id: UUID, request: Request):
    return FileResponse(request.app.state.frontend_dir / "analyses.html")
