"""Compose the web backend and serve its sibling browser application."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .adapter.mysql import MySQLClient, MySQLConfig
from .adapter.mysql.analysis_repository import MySQLAnalysisRepository
from .adapter.sqlite.analytics_repository import AnalyticsRepository, SQLiteAnalysisRepository
from .middleware import register_middleware
from .routes import auth as auth_routes
from .routes import jobs as job_routes
from .routes import analyses, analytics, chats, database, models, pages, runs
from .services.analysis import AnalysisService
from .services.analytics import AnalyticsService
from .services.auth import AuthManager
from .services.auth_jobs import AuthJobs
from .services.chat import ChatManager
from .tools.registry import create_tool_executor, list_tool_definitions


def create_app(auth: AuthManager | None = None, *, data_dir: Path | None = None, analysis_storage: str = "mysql") -> FastAPI:
    if analysis_storage not in {"mysql", "sqlite"}:
        raise ValueError("Armazenamento de análises inválido.")
    auth = auth or AuthManager()
    data_root = data_dir or ((auth.auth_home / "web") if auth.auth_home else Path.cwd() / ".harness-eng")
    mysql_config = data_root / "mysql.json"
    mysql = MySQLClient.from_env(config_file=mysql_config) if analysis_storage == "mysql" and MySQLConfig.is_configured(config_file=mysql_config) else None
    analytics_path = data_root / "analytics.sqlite3"
    analysis = AnalysisService(SQLiteAnalysisRepository(analytics_path)) if analysis_storage == "sqlite" else AnalysisService(MySQLAnalysisRepository(mysql)) if mysql is not None else None
    analytics_service = AnalyticsService(analysis, AnalyticsRepository(analytics_path)) if analysis is not None else None
    tool_executor = create_tool_executor(analysis, analytics_service=analytics_service)
    tool_definitions = list_tool_definitions(analysis, analytics_service=analytics_service)
    jobs = AuthJobs(auth)
    chat = ChatManager(auth, data_root, tool_executor=tool_executor, tool_definitions=tool_definitions)
    frontend_dir = Path(__file__).resolve().parent.parent / "app"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await asyncio.to_thread(jobs.close)
            await asyncio.to_thread(chat.close)

    app = FastAPI(title="Harness Eng", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth = auth
    app.state.jobs = jobs
    app.state.chat = chat
    app.state.mysql = mysql
    app.state.analysis = analysis
    app.state.analytics = analytics_service
    app.state.analysis_storage = analysis_storage
    app.state.tool_executor = tool_executor
    app.state.tool_definitions = tool_definitions
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.state.frontend_dir = frontend_dir
    register_middleware(app)

    for router in (pages.router, auth_routes.router, job_routes.router, models.router, chats.router, runs.router, database.router, analyses.router, analytics.router):
        app.include_router(router)
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    return app
