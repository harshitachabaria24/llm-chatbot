"""FastAPI application: /health, /chat, /sessions/{id}/messages."""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .db import Database
from .llm import AllProvidersUnavailable, LLMGateway, parse_failure_header
from .schemas import (
    ChatMetadata,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    HistoryResponse,
)

logger = logging.getLogger("chatbot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)
    gateway: LLMGateway | None = None
    session_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal gateway
        db.init()  # schema is created automatically on startup
        gateway = LLMGateway(settings)
        await gateway.warm_up()
        if gateway.keys_configured < 2:
            logger.warning("Only %d API key(s) configured; failover needs at least 2", gateway.keys_configured)
        yield

    app = FastAPI(title="LLM Chatbot", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- error handling: always JSON with a `detail` field, never a trace ----
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    # -- endpoints ------------------------------------------------------------
    @app.get("/health", response_model=HealthResponse)
    async def health():
        try:
            await asyncio.to_thread(db.ping)
        except Exception:  # noqa: BLE001
            return JSONResponse(
                {"status": "error", "keys_configured": 0, "cache_enabled": settings.cache_enabled,
                 "detail": "database unreachable"},
                status_code=503,
            )
        keys = gateway.keys_configured if gateway else 0
        if keys == 0:
            return JSONResponse(
                {"status": "error", "keys_configured": 0, "cache_enabled": settings.cache_enabled,
                 "detail": "no API keys configured"},
                status_code=503,
            )
        return HealthResponse(status="ok", keys_configured=keys, cache_enabled=settings.cache_enabled)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        body: ChatRequest,
        x_simulate_failure: str | None = Header(default=None),
    ):
        started = time.perf_counter()
        assert gateway is not None

        simulate = parse_failure_header(x_simulate_failure) if settings.enable_failure_simulation else {}

        lock = session_locks.get(body.session_id)
        if lock is None:
            lock = asyncio.Lock()
            session_locks[body.session_id] = lock

        async with lock:  # serialize turns within one session
            stored_prompt = await asyncio.to_thread(db.get_system_prompt, body.session_id)
            system_prompt = body.system_prompt or stored_prompt or settings.default_system_prompt
            history = await asyncio.to_thread(
                db.get_history, body.session_id, settings.max_history_messages
            )
            messages = [{"role": "system", "content": system_prompt}]
            messages += [{"role": m["role"], "content": m["content"]} for m in history]
            messages.append({"role": "user", "content": body.message})

            try:
                result = await gateway.complete(messages, simulate)
            except AllProvidersUnavailable:
                return JSONResponse({"detail": "all providers unavailable"}, status_code=503)

            metadata = ChatMetadata(
                resolution_source="LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
                model=result.model,
                latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                key_id=result.key_id,
                fallback_attempts=result.fallback_attempts,
            )
            await asyncio.to_thread(
                db.save_exchange,
                body.session_id,
                body.message,
                result.reply,
                metadata.model_dump(),
                body.system_prompt,
            )

        return ChatResponse(session_id=body.session_id, reply=result.reply, metadata=metadata)

    @app.get("/sessions/{session_id}/messages", response_model=HistoryResponse)
    async def session_messages(session_id: str):
        if not await asyncio.to_thread(db.session_exists, session_id):
            return JSONResponse({"detail": "session not found"}, status_code=404)
        messages = await asyncio.to_thread(db.get_history, session_id)
        return HistoryResponse(session_id=session_id, messages=messages)

    return app


app = create_app()
