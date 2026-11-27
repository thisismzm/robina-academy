"""
OpenTelemetry tracing.

Off by default. It turns on when an OTLP endpoint is configured through the
standard OpenTelemetry environment variables, so the same build runs with or
without a collector:

    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # OTLP/HTTP (Tempo, Alloy, Grafana Cloud)
    OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic ...  # Grafana Cloud only
    OTEL_SERVICE_NAME=learnhouse-api                    # optional, this is the default
    OTEL_TRACES_SAMPLER=parentbased_traceidratio        # optional
    OTEL_TRACES_SAMPLER_ARG=0.2                         # optional

The SDK reads every OTEL_* variable itself; this module only decides whether to
start and which libraries to instrument. Set OTEL_SDK_DISABLED=true to force it
off even when an endpoint is present.
"""

import logging
import os

from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Probes and the liveness check fire every few seconds and carry no signal.
_EXCLUDED_URLS = "health"

_provider = None


def is_telemetry_enabled() -> bool:
    if os.getenv("TESTING", "").lower() in ("true", "1", "yes"):
        return False
    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    )


def setup_telemetry(app: FastAPI, environment: str, version: str) -> None:
    """Configure the tracer provider and instrument the app and its clients."""
    global _provider
    if _provider is not None or not is_telemetry_enabled():
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from src.core.events.database import engine

        # OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES are merged in by
        # Resource.create(); the values below are only defaults.
        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "learnhouse-api"),
                "service.version": version,
                "deployment.environment.name": environment,
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=provider,
            excluded_urls=_EXCLUDED_URLS,
        )
        SQLAlchemyInstrumentor().instrument(
            engine=engine.sync_engine, tracer_provider=provider
        )
        RedisInstrumentor().instrument(tracer_provider=provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        RequestsInstrumentor().instrument(tracer_provider=provider)

        _provider = provider
        logger.info("OpenTelemetry tracing enabled")
    except Exception:
        # Observability must never stop the API from booting.
        logger.exception("Failed to initialise OpenTelemetry; continuing without it")


def shutdown_telemetry() -> None:
    """Flush buffered spans. Called on application shutdown."""
    global _provider
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception:
        logger.exception("Failed to flush OpenTelemetry spans")
    finally:
        _provider = None
