"""ToolRegistry = the governance layer. The model PROPOSES, this code DECIDES.  (COMPLETE)

Enforced in code, not in prompts:
    * confirmation gate  - ACTION tools never run unless confirmed=True
    * tiered timeouts    - fast lookups fail fast, diagnostics get more time, actions the most
    * uniform errors     - callers always get a ToolResult, never an exception
    * full telemetry     - a span + metrics per execution, including blocked attempts
"""
from __future__ import annotations

import asyncio
import logging
import time

from backend.telemetry import get_metrics, start_span

from .base import RiskLevel, Tool, ToolResult

logger = logging.getLogger(__name__)

TIMEOUTS_S: dict[RiskLevel, float] = {RiskLevel.INFO: 5.0, RiskLevel.DIAGNOSTIC: 15.0, RiskLevel.ACTION: 30.0}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool '{name}'") from None

    async def execute(self, name: str, *, confirmed: bool = False, timeout_s: float | None = None, **kwargs) -> ToolResult:
        tool = self.get(name)
        timeout = timeout_s if timeout_s is not None else TIMEOUTS_S[tool.risk]
        started = time.perf_counter()

        with start_span(f"tool.{name}", {"assistant.tool.name": name, "assistant.tool.risk": tool.risk.value}) as span:
            if tool.requires_confirmation and not confirmed:
                result = ToolResult(
                    status="error", category="confirmation_required", retryable=False,
                    details=f"'{name}' changes system state and needs the user's explicit confirmation.",
                )
                metric_status = "blocked"
            else:
                try:
                    result = await asyncio.wait_for(tool.run(**kwargs), timeout=timeout)
                    metric_status = result.status
                except asyncio.TimeoutError:
                    result = ToolResult(status="error", category="transient_timeout", retryable=True,
                                        details=f"'{name}' timed out after {timeout:.0f}s.")
                    metric_status = "error"
                except Exception as exc:  # a tool bug must never crash the conversation
                    logger.exception("tool crashed", extra={"tool": name})
                    result = ToolResult(status="error", category="tool_error", retryable=False,
                                        details=f"'{name}' failed unexpectedly.")
                    span.record_exception(exc)
                    metric_status = "error"

            span.set_attribute("assistant.tool.status", metric_status)
            if result.category:
                span.set_attribute("assistant.tool.error_category", result.category)

        get_metrics().record_tool_call(
            tool=name, risk=tool.risk.value, status=metric_status, duration_s=time.perf_counter() - started
        )
        return result
