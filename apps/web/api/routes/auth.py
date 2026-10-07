"""Bootstrap, provider status and account login/logout."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import AuthService, JobsService, mutation
from ..domain.auth import Provider
from ..domain.errors import JobConflict
from ..domain.jobs import public_status
from .schemas import LoginRequest

router = APIRouter(prefix="/api")


@router.get("/bootstrap")
async def bootstrap(request: Request, auth: AuthService):
    return {
        "csrf_token": request.app.state.csrf_token,
        "scope": "isolated" if auth.auth_home else "existing",
    }


@router.get("/status")
async def status(auth: AuthService, jobs: JobsService):
    statuses = await asyncio.gather(
        *(asyncio.to_thread(auth.status, provider) for provider in Provider)
    )
    return {"providers": [public_status(item) for item in statuses], "active_jobs": jobs.active()}


@router.post("/auth/{provider}/login", status_code=202, dependencies=[Depends(mutation)])
async def login(provider: Provider, body: LoginRequest, jobs: JobsService):
    if provider == Provider.CLAUDE and body.mode == "device":
        raise HTTPException(400, "Use o login pelo navegador para Claude.")
    try:
        return {"job": jobs.start(provider, "login", mode=body.mode, force=body.force)}
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/auth/{provider}/logout", status_code=202, dependencies=[Depends(mutation)])
async def logout(provider: Provider, jobs: JobsService):
    try:
        return {"job": jobs.start(provider, "logout")}
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from None
