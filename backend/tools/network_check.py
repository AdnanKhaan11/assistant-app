"""Simulate read-only network diagnostics with deterministic sample readings."""
from __future__ import annotations

import asyncio

from .base import RiskLevel, Tool, ToolResult


class NetworkCheckTool(Tool):
    """Return plausible simulated network readings without changing system state."""

    name = "network_check"
    description = "Run a network diagnostic (latency, packet loss, DNS)."
    risk = RiskLevel.DIAGNOSTIC

    async def run(self, **_: object) -> ToolResult:
        """Wait briefly, then return stable packet loss, latency, and DNS readings."""
        await asyncio.sleep(0.3)
        return ToolResult("ok", "packet loss 7%, latency 180ms, DNS ok")
