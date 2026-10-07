"""Compose the web backend and serve its sibling browser application."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .middleware import register_middleware
from .routes import auth as auth_routes
from .routes import jobs as job_routes
from .routes import chats, models, pages, runs
from .services.auth import AuthManager
from .services.auth_jobs import AuthJobs
from .services.chat import ChatManager


def create_app(auth: AuthManager | None = None, *, data_dir: Path | None = None) -> FastAPI:
    auth = auth or AuthManager()
    jobs = AuthJobs(auth)
    chat = ChatManager(auth, data_dir or ((auth.auth_home / "web") if auth.auth_home else Path.cwd() / ".harness-eng"))
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
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.state.frontend_dir = frontend_dir
    register_middleware(app)

    for router in (pages.router, auth_routes.router, job_routes.router, models.router, chats.router, runs.router):
        app.include_router(router)
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    return app
