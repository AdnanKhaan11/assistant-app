"""Simulate an idempotent network service restart after registry confirmation."""
from __future__ import annotations

import asyncio

from .base import RiskLevel, Tool, ToolResult

_RESTARTED: set[str] = set()


class NetworkActionTool(Tool):
    """Model a per-ticket network restart whose execution is confirmation-gated."""

    name = "network_action"
    description = "Restart the network service (changes system state)."
    risk = RiskLevel.ACTION

    async def run(self, ticket_id: str = "", **_: object) -> ToolResult:
        """Simulate a restart once per ticket and report repeated requests safely."""
        if ticket_id in _RESTARTED:
            return ToolResult(
                "ok",
                "Network service already restarted for this ticket - nothing to do.",
            )

        await asyncio.sleep(0.5)
        _RESTARTED.add(ticket_id)
        return ToolResult("ok", "Network service restarted.")
