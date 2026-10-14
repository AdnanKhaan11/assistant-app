"""Store bounded, per-ticket conversation state in process memory."""
from __future__ import annotations

from collections import deque

from backend.llm import Message
from backend.schemas import PendingAction


class ShortTermMemory:
    """Keep recent messages, summaries, and awaiting actions isolated by ticket."""

    def __init__(self, window_turns: int = 10) -> None:
        if window_turns < 0:
            raise ValueError("window_turns cannot be negative")
        self._window = window_turns * 2
        self._messages: dict[str, deque[Message]] = {}
        self._summaries: dict[str, str] = {}
        self._pending: dict[str, PendingAction] = {}

    def add(self, ticket_id: str, message: Message) -> None:
        """Append one message while retaining only the configured history window."""
        history = self._messages.get(ticket_id)
        if history is None:
            history = deque(maxlen=self._window)
            self._messages[ticket_id] = history
        history.append(message)

    def history(self, ticket_id: str) -> list[Message]:
        """Return a new list so callers cannot modify the stored conversation."""
        return list(self._messages.get(ticket_id, ()))

    def set_summary(self, ticket_id: str, summary: str) -> None:
        """Replace the rolling summary for one ticket."""
        self._summaries[ticket_id] = summary

    def get_summary(self, ticket_id: str) -> str:
        """Return the ticket summary, or an empty string when none exists."""
        return self._summaries.get(ticket_id, "")

    def set_pending(self, action: PendingAction) -> None:
        """Store an action under its single-use identifier."""
        self._pending[action.action_id] = action

    def pop_pending(self, action_id: str) -> PendingAction | None:
        """Remove and return a pending action so it cannot be confirmed twice."""
        return self._pending.pop(action_id, None)

    def clear(self, ticket_id: str) -> None:
        """Remove all in-memory state associated with a completed ticket."""
        self._messages.pop(ticket_id, None)
        self._summaries.pop(ticket_id, None)
        for action_id in [
            action_id for action_id, action in self._pending.items() if action.ticket_id == ticket_id
        ]:
            self._pending.pop(action_id, None)

    def active_tickets(self) -> int:
        """Count tickets that currently have a short-term message buffer."""
        return len(self._messages)
