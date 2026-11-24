"""FastAPI app: GET /health, POST /chat, GET /sessions/{session_id}/messages."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import load_settings
from .db import Database
from .gateway import AllProvidersUnavailable, LLMGateway
from .schemas import ChatMetadata, ChatRequest, ChatResponse
from .simulation import parse_simulation_header

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    db = Database(settings.database_path)
    db.init()
    app.state.settings = settings
    app.state.db = db
    app.state.gateway = LLMGateway(settings)
    log.info(
        "started: keys=%d models=%s cache=%s simulation=%s",
        len(settings.keys), settings.models, settings.cache_type, settings.enable_failure_simulation,
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Lyftr LLM Chat", version="1.0.0", lifespan=lifespan)

    # CORS is read at import time so the middleware can be registered up-front.
    origins = load_settings().allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # ---- error handlers: always JSON with `detail`, never a stack trace ----
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = [
            {k: v for k, v in e.items() if k in ("type", "loc", "msg")} for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # ---- routes -----------------------------------------------------------
    @app.get("/health")
    async def health(request: Request):
        settings = request.app.state.settings
        db_ok = await run_in_threadpool(request.app.state.db.ping)
        keys = len(settings.keys)
        ok = db_ok and keys > 0
        body = {"status": "ok" if ok else "degraded", "keys_configured": keys, "cache_enabled": True}
        if not ok:
            body["detail"] = "database unreachable" if not db_ok else "no API keys configured"
        return JSONResponse(status_code=200 if ok else 503, content=body)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        body: ChatRequest,
        request: Request,
        x_simulate_failure: str | None = Header(default=None),
    ):
        started = time.perf_counter()
        state = request.app.state
        settings, db, gateway = state.settings, state.db, state.gateway

        simulate = {}
        if settings.enable_failure_simulation:
            simulate = parse_simulation_header(x_simulate_failure)

        stored_prompt = await run_in_threadpool(db.get_system_prompt, body.session_id)
        system_prompt = body.system_prompt or stored_prompt or settings.default_system_prompt
        history = await run_in_threadpool(db.history, body.session_id, settings.history_limit)

        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": body.message})

        try:
            result = await gateway.complete(messages, simulate=simulate)
        except AllProvidersUnavailable as exc:
            log.error("all providers unavailable: %s", exc.attempts)
            return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

        metadata = ChatMetadata(
            resolution_source=result.resolution_source,
            model=result.model,
            latency_ms=int(round((time.perf_counter() - started) * 1000)),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            key_id=result.key_id,
            fallback_attempts=result.fallback_attempts,
        )
        await run_in_threadpool(
            db.save_exchange,
            body.session_id,
            body.message,
            result.reply,
            metadata.model_dump(),
            body.system_prompt,
        )
        return ChatResponse(session_id=body.session_id, reply=result.reply, metadata=metadata)

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str, request: Request):
        db = request.app.state.db
        if not await run_in_threadpool(db.session_exists, session_id):
            return JSONResponse(status_code=404, content={"detail": "session not found"})
        messages = await run_in_threadpool(db.history, session_id)
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
