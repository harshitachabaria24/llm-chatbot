"""Request/response models matching the API contract exactly."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints

SessionId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
SystemPrompt = Annotated[str, StringConstraints(strip_whitespace=True, max_length=8000)]


class ChatRequest(BaseModel):
    session_id: SessionId
    message: Message
    system_prompt: Optional[SystemPrompt] = None


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str] = None
    fallback_attempts: int = 0


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class HealthResponse(BaseModel):
    status: str
    keys_configured: int
    cache_enabled: bool


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: Optional[dict[str, Any]] = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]


class ErrorResponse(BaseModel):
    detail: Any = Field(...)
