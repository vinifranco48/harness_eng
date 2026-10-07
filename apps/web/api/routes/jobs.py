"""Track and control native authentication jobs."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import JobsService, mutation
from ..domain.errors import JobInputError
from .schemas import CodeRequest

router = APIRouter(prefix="/api/jobs")


@router.get("/{job_id}")
async def job(job_id: str, jobs: JobsService):
    try:
        return {"job": jobs.get(job_id)}
    except KeyError:
        raise HTTPException(404, "Esta operação não foi encontrada.") from None


@router.post("/{job_id}/cancel", dependencies=[Depends(mutation)])
async def cancel(job_id: str, jobs: JobsService):
    try:
        return {"job": await asyncio.to_thread(jobs.cancel, job_id)}
    except KeyError:
        raise HTTPException(404, "Esta operação não foi encontrada.") from None


@router.post("/{job_id}/code", dependencies=[Depends(mutation)])
async def code(job_id: str, body: CodeRequest, jobs: JobsService):
    try:
        return {"job": await asyncio.to_thread(jobs.send_code, job_id, body.code)}
    except KeyError:
        raise HTTPException(404, "Esta operação não foi encontrada.") from None
    except JobInputError as exc:
        raise HTTPException(400, str(exc)) from None
