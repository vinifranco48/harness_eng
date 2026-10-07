"""Request validation at the HTTP boundary."""

from typing import Literal

from pydantic import BaseModel, Field

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
