"""Request validation at the HTTP boundary."""

from typing import Literal

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..domain.auth import Provider


class LoginRequest(BaseModel):
    mode: Literal["browser", "device"] = "browser"
    force: bool = False


class CodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)


class ChatRequest(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=150)
    effort: str | None = Field(default=None, max_length=20)


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=32000)
    model: str = Field(min_length=1, max_length=150)
    effort: str | None = Field(default=None, max_length=20)


class AnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str


class AnalysisResponse(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class AnalysisEnvelope(BaseModel):
    analysis: AnalysisResponse


class AnalysesEnvelope(BaseModel):
    analyses: list[AnalysisResponse]
    limit: int
    offset: int
