"""Upload tabular data and expose saved Vega-Lite charts."""

import asyncio
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError

from ..dependencies import mutation
from ..domain.analysis import AnalysisNotFound, AnalysisStorageError, AnalysisValidationError
from ..services.analytics import MAX_FILE_BYTES, AnalyticsService, ChartCreateArgs

router = APIRouter(prefix="/api")


def get_analytics(request: Request) -> AnalyticsService:
    service = getattr(request.app.state, "analytics", None)
    if service is None:
        raise HTTPException(503, "Configure o armazenamento das análises para continuar.")
    return service


AnalyticsDependency = Annotated[AnalyticsService, Depends(get_analytics)]


async def _run(operation: Callable, *args, **kwargs):
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except (AnalysisValidationError, ValidationError) as error:
        detail = str(error) if isinstance(error, AnalysisValidationError) else "Confira os argumentos do gráfico."
        raise HTTPException(422, detail) from None
    except AnalysisNotFound as error:
        raise HTTPException(404, str(error)) from None
    except AnalysisStorageError:
        raise HTTPException(503, "Não foi possível acessar os dados da análise. Tente novamente.") from None


@router.get("/datasets")
async def list_datasets(
    analytics: AnalyticsDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
):
    return {"datasets": await _run(analytics.list_datasets, limit=limit, offset=offset), "limit": limit, "offset": offset}


@router.post("/datasets", status_code=201, dependencies=[Depends(mutation)])
async def import_dataset(
    analytics: AnalyticsDependency,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form(max_length=120)] = None,
):
    try:
        content = await file.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, "Envie um arquivo de até 5 MiB.")
        dataset = await _run(analytics.import_dataset, content, file.filename or "", name)
        return {"dataset": dataset}
    finally:
        await file.close()


@router.post("/datasets/demo", status_code=201, dependencies=[Depends(mutation)])
async def import_demo(analytics: AnalyticsDependency):
    return {"dataset": await _run(analytics.import_demo)}


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: UUID, analytics: AnalyticsDependency):
    return {"dataset": await _run(analytics.get_dataset, str(dataset_id))}


@router.get("/analyses/{analysis_id}/charts")
async def list_charts(analysis_id: UUID, analytics: AnalyticsDependency):
    return {"charts": await _run(analytics.list_charts, str(analysis_id))}


@router.post("/analyses/{analysis_id}/charts", status_code=201, dependencies=[Depends(mutation)])
async def create_chart(analysis_id: UUID, body: ChartCreateArgs, analytics: AnalyticsDependency):
    return {"chart": await _run(analytics.create_chart, str(analysis_id), body)}


@router.get("/charts/{chart_id}")
async def get_chart(chart_id: UUID, analytics: AnalyticsDependency):
    return {"chart": await _run(analytics.get_chart, str(chart_id))}
