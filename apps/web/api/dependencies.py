"""HTTP dependencies backed by the application's service instances."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from .services.auth import AuthManager
from .services.auth_jobs import AuthJobs
from .services.chat import ChatManager


def get_auth(request: Request) -> AuthManager:
    return request.app.state.auth


def get_jobs(request: Request) -> AuthJobs:
    return request.app.state.jobs


def get_chat(request: Request) -> ChatManager:
    return request.app.state.chat


async def mutation(request: Request) -> None:
    supplied = request.headers.get("x-harness-csrf", "")
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if not secrets.compare_digest(supplied, request.app.state.csrf_token) or (
        origin is not None and origin != expected
    ):
        raise HTTPException(403, "Recarregue esta página para continuar.")


AuthService = Annotated[AuthManager, Depends(get_auth)]
JobsService = Annotated[AuthJobs, Depends(get_jobs)]
ChatService = Annotated[ChatManager, Depends(get_chat)]
