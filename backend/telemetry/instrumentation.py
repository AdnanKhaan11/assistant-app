"""Auto-instrumentation: free spans for the web framework and outgoing HTTP calls.

    FastAPI  -> one server span per request (route, status code, duration) + http.server metrics
    httpx    -> one client span per call to an LLM provider (latency, status)  -> shows where time goes

SECURITY: httpx spans record the URL. Never put API keys in query strings (use headers).
"""
from __future__ import annotations

from typing import Any


def instrument_fastapi(app: Any, excluded_urls: str = "health,metrics") -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    # excluded_urls: comma separated regex - health checks would otherwise create thousands of useless traces
    FastAPIInstrumentor.instrument_app(app, excluded_urls=excluded_urls)


def instrument_httpx() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()
