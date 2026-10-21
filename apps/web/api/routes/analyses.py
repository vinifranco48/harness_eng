"""HTTP entry points for persistent analytical reports."""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import AnalysisService, mutation
from ..domain.analysis import AnalysisNotFound, AnalysisStorageError, AnalysisValidationError
from .schemas import AnalysesEnvelope, AnalysisCreateRequest, AnalysisEnvelope

router = APIRouter(prefix="/api/analyses")


@router.post("", status_code=201, response_model=AnalysisEnvelope, dependencies=[Depends(mutation)])
async def create_analysis(body: AnalysisCreateRequest, analysis: AnalysisService):
    try:
        result = await asyncio.to_thread(analysis.create, body.title)
        return {"analysis": result.public()}
    except AnalysisValidationError as error:
        raise HTTPException(422, str(error)) from None
    except AnalysisStorageError:
        raise HTTPException(503, "Não foi possível acessar as análises. Tente novamente.") from None


@router.get("", response_model=AnalysesEnvelope)
async def list_analyses(
    analysis: AnalysisService,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
):
    try:
        results = await asyncio.to_thread(analysis.list, limit=limit, offset=offset)
        return {"analyses": [result.public() for result in results], "limit": limit, "offset": offset}
    except AnalysisValidationError as error:
        raise HTTPException(422, str(error)) from None
    except AnalysisStorageError:
        raise HTTPException(503, "Não foi possível acessar as análises. Tente novamente.") from None


@router.get("/{analysis_id}", response_model=AnalysisEnvelope)
async def get_analysis(analysis_id: UUID, analysis: AnalysisService):
    try:
        result = await asyncio.to_thread(analysis.get, str(analysis_id))
        return {"analysis": result.public()}
    except AnalysisNotFound:
        raise HTTPException(404, "Esta análise não foi encontrada.") from None
    except AnalysisStorageError:
        raise HTTPException(503, "Não foi possível acessar as análises. Tente novamente.") from None
