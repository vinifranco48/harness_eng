"""Inspect the configured application database connection."""

import asyncio

from fastapi import APIRouter, HTTPException

from ..adapter.mysql import MySQLConfigurationError, MySQLConnectionError
from ..dependencies import MySQLService

router = APIRouter(prefix="/api/database")


@router.get("/status")
async def database_status(mysql: MySQLService):
    try:
        return await asyncio.to_thread(mysql.check_connection)
    except (MySQLConfigurationError, MySQLConnectionError):
        raise HTTPException(503, "Não foi possível conectar ao banco de dados.") from None
