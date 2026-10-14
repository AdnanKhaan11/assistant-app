"""Read known ticket details from the local JSON ticket fixture."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .base import RiskLevel, Tool, ToolResult

logger = logging.getLogger(__name__)


class IssueLookupTool(Tool):
    """Provide cached, read-only ticket lookups without blocking the event loop."""

    name = "issue_lookup"
    description = "Look up the stored details of a ticket by id (read-only)."
    risk = RiskLevel.INFO

    def __init__(self, tickets_path: str) -> None:
        self._path = Path(tickets_path)
        self._tickets: dict[str, str] | None = None
        self._load_lock = asyncio.Lock()

    async def _get_tickets(self) -> dict[str, str]:
        async with self._load_lock:
            if self._tickets is None:
                self._tickets = await asyncio.to_thread(self._load)
            return self._tickets

    def _load(self) -> dict[str, str]:
        with self._path.open("r", encoding="utf-8") as ticket_file:
            records = json.load(ticket_file)
        if not isinstance(records, list):
            raise ValueError("Ticket data must be a list")
        tickets: dict[str, str] = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Ticket records must be objects")
            ticket_id, details = record.get("id"), record.get("details")
            if not isinstance(ticket_id, str) or not isinstance(details, str):
                raise ValueError("Ticket records must contain text id and details")
            tickets[ticket_id] = details
        return tickets

    async def run(self, ticket_id: str = "", **_: object) -> ToolResult:
        """Return details for a matching ticket or a categorized safe failure."""
        try:
            tickets = await self._get_tickets()
        except (OSError, json.JSONDecodeError, ValueError):
            logger.exception("ticket lookup data could not be loaded")
            return ToolResult("error", "Ticket details are temporarily unavailable.", category="tool_error")

        details = tickets.get(ticket_id)
        if details is None:
            return ToolResult("error", f"No ticket {ticket_id}", category="not_found")
        return ToolResult("ok", details)
