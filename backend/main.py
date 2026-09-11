"""FastAPI application entry point.

Startup order matters and is deliberate:

1. Logging, so everything after it is visible.
2. Tracing, so the MCP subprocess launches are themselves traced.
3. MCP servers, spawned concurrently -- this is the slowest step (~2-5s) and
   everything downstream depends on it.
4. The compiled graph with its checkpointer.
5. A background warm-up that prefetches the multi-megabyte airport dataset and
   the FX rates, so the first real request is not the one that pays for them.

Startup never hard-fails on a degraded dependency. A missing LLM key, a down
MCP server, or an unreachable OTel collector each degrade one capability; the
service still starts and ``/health`` reports exactly what is missing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import routes_chat, routes_session, routes_voice
from .config import settings
from .graph import close_graph, get_compiled_graph
from .logging_setup import setup_logging
from .mcp import mcp_manager
from .mcp.tools import warm_up
from .observability import langfuse_hook, setup_tracing, shutdown_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown."""
    setup_logging()
    logger.info("=" * 68)
    logger.info("Wayfarer Travel Assistant -- starting (env=%s)", settings.app_env)
    logger.info("=" * 68)

    setup_tracing(app)

    logger.info("Starting MCP servers: %s", ", ".join(settings.enabled_servers))
    try:
        statuses = await mcp_manager.startup()
        for name, state in statuses.items():
            level = logging.INFO if state == "ready" else logging.WARNING
            logger.log(level, "  MCP %-10s %s", name, state)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("MCP startup failed entirely: %s", exc)

    try:
        await get_compiled_graph()
        logger.info("Agent graph compiled.")
    except Exception as exc:  # pragma: no cover
        logger.error("Graph compilation failed: %s", exc)

    if settings.llm_available:
        logger.info("LLM enabled: %s (fast: %s)",
                    settings.hf_chat_model, settings.hf_fast_model)
    else:
        logger.warning(
            "No HUGGINGFACE_API_KEY. Running on the deterministic rule-based "
            "path -- fully functional, less nuanced."
        )

    # Warm the slow caches without blocking readiness.
    warm_task = asyncio.create_task(_warm_up_background())

    logger.info("Ready on http://%s:%d", settings.backend_host, settings.backend_port)

    try:
        yield
    finally:
        logger.info("Shutting down...")
        warm_task.cancel()
        try:
            await warm_task
        except (asyncio.CancelledError, Exception):
            pass

        await close_graph()
        await mcp_manager.shutdown()

        try:
            from .llm.hf_client import llm

            await llm.aclose()
        except Exception:  # pragma: no cover
            pass

        langfuse_hook.flush()
        shutdown_tracing()
        logger.info("Shutdown complete.")


async def _warm_up_background() -> None:
    """Prefetch slow datasets after startup, tolerating failure."""
    try:
        await asyncio.sleep(0.5)  # Let the server bind first.
        result = await warm_up()
        logger.info("Warm-up complete: %s", result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("Warm-up skipped: %s", exc)


app = FastAPI(
    title="Wayfarer Travel Assistant",
    description=(
        "A multi-agent travel planner built on LangGraph and the Model Context "
        "Protocol, using only keyless open-data sources plus Hugging Face for "
        "language reasoning."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(routes_session.router)
app.include_router(routes_chat.router)
app.include_router(routes_voice.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a useful error without leaking internals to the client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred. Check the server logs.",
            "path": request.url.path,
        },
    )


@app.get("/", include_in_schema=False)
async def root() -> dict[str, object]:
    """Service banner with the links a newcomer needs."""
    return {
        "service": "Wayfarer Travel Assistant",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
        "graph": "/api/graph",
        "mcp_tools": "/api/mcp/tools",
        "data_sources": {
            "weather": "Open-Meteo (keyless, CC-BY)",
            "geocoding": "Open-Meteo Geocoding + OSM Nominatim (keyless, ODbL)",
            "places": "OpenStreetMap Overpass (keyless, ODbL)",
            "airports": "OurAirports (public domain)",
            "exchange_rates": "Frankfurter / ECB (keyless)",
            "countries": "Bundled ISO datasets",
            "llm": settings.hf_chat_model if settings.llm_available else "disabled",
        },
        "disclaimer": (
            "Flight and lodging prices are modelled estimates from open data, "
            "not live bookable fares."
        ),
    }


def run() -> None:
    """Entry point for ``python -m app.main``."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=settings.app_env == "development",
        log_config=None,  # We install our own logging.
    )


if __name__ == "__main__":
    run()
