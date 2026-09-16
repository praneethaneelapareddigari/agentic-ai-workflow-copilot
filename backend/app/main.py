from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_conversations import router as conversations_router
from app.api.routes_workflows import router as workflows_router
from app.cache import cache_manager
from app.config import get_settings
from app.database import init_db
from app.observability import get_logger
from app.schemas import HealthResponse

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info(f"{settings.APP_NAME} started (env={settings.ENV}, mock_llm={settings.USE_MOCK_LLM})")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Agentic AI workflow copilot: decomposes requests into an "
                 "8-stage pipeline that dynamically invokes 12+ external tools.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows_router, prefix=settings.API_PREFIX)
app.include_router(conversations_router, prefix=settings.API_PREFIX)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    # Touch the cache once so backend_name reflects reality even before any
    # workflow has run.
    await cache_manager.get_json("healthcheck")
    return HealthResponse(
        status="ok",
        llm_mode="mock" if settings.USE_MOCK_LLM else "openai",
        cache_backend=cache_manager.backend_name,
    )
