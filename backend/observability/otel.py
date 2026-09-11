"""OpenTelemetry wiring.

Design decisions worth knowing:

* **Tracing is optional and never fatal.** If the collector is down, spans are
  dropped and the request still succeeds. An observability stack that can take
  production down is worse than no observability stack.
* **The no-op path is real.** When ``OTEL_ENABLED=false`` we install a tracer
  that satisfies the same interface, so instrumentation call sites need no
  ``if enabled:`` guards.
* **Spans follow the graph.** Each agent node opens one span named
  ``agent.<node>``; each MCP call opens ``mcp.<server>.<tool>``. A trace of one
  conversation therefore reads as the agent's actual decision path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

_initialised = False
_tracer: Any = None


class _NoopSpan:
    """Stands in for a span when tracing is disabled or unavailable."""

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_attributes(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add_event(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, *_args: Any, **_kwargs: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


def setup_tracing(app: Any = None) -> None:
    """Initialise the tracer provider and optional FastAPI/httpx instrumentation.

    Safe to call more than once; safe to call when the OTel packages are not
    installed at all.
    """
    global _initialised, _tracer

    if _initialised:
        return
    _initialised = True

    if not settings.otel_enabled:
        _tracer = _NoopTracer()
        logger.info("OpenTelemetry disabled by configuration.")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _tracer = _NoopTracer()
        logger.warning("OpenTelemetry SDK not installed; tracing disabled.")
        return

    try:
        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": "1.0.0",
                "deployment.environment": settings.app_env,
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("wayfarer")

        # Auto-instrument the edges: inbound HTTP and outbound HTTP.
        if app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

                FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/metrics")
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.debug("FastAPI instrumentation unavailable: %s", exc)
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.debug("httpx instrumentation unavailable: %s", exc)

        logger.info(
            "OpenTelemetry tracing enabled -> %s", settings.otel_exporter_otlp_endpoint
        )
    except Exception as exc:  # pragma: no cover - defensive
        _tracer = _NoopTracer()
        logger.warning("Failed to initialise tracing (%s); continuing without it.", exc)


def get_tracer() -> Any:
    """Return the active tracer, initialising lazily if needed."""
    global _tracer
    if _tracer is None:
        setup_tracing()
    return _tracer or _NoopTracer()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open a span with attributes; tracing failures never break the caller.

    Only span *creation* is guarded. Exceptions raised by the wrapped body must
    propagate untouched -- LangGraph implements ``interrupt()`` by raising
    ``GraphInterrupt`` as control flow, and a tracing wrapper that swallowed or
    re-yielded on exception would break every human-in-the-loop pause with
    "generator didn't stop after throw()".
    """
    tracer = get_tracer()
    try:
        manager = tracer.start_as_current_span(name)
    except Exception:  # pragma: no cover - tracer misconfigured
        manager = None

    if manager is None:
        yield _NoopSpan()
        return

    with manager as current:
        for key, value in attributes.items():
            if value is not None:
                try:
                    current.set_attribute(key, value)
                except Exception:  # pragma: no cover
                    pass
        yield current


def shutdown_tracing() -> None:
    """Flush pending spans on shutdown so the last trace is not lost."""
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:  # pragma: no cover
        pass
