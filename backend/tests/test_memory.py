from __future__ import annotations

from pathlib import Path

import pytest

from backend.llm import Message
from backend.memory.long_term import LongTermMemory
from backend.memory.short_term import ShortTermMemory
from backend.schemas import PendingAction


def test_short_term_bounds_history_per_ticket_and_returns_a_copy() -> None:
    memory = ShortTermMemory(window_turns=2)
    for index in range(6):
        memory.add("ticket-a", Message("user", f"message-{index}"))
    memory.add("ticket-b", Message("user", "separate"))

    history = memory.history("ticket-a")
    history.append(Message("assistant", "outside mutation"))

    assert [message.content for message in memory.history("ticket-a")] == [
        "message-2",
        "message-3",
        "message-4",
        "message-5",
    ]
    assert [message.content for message in memory.history("ticket-b")] == ["separate"]
    assert memory.active_tickets() == 2


def test_short_term_summaries_and_pending_actions_are_ticket_scoped() -> None:
    memory = ShortTermMemory()
    action_a = PendingAction(
        action_id="action-a",
        ticket_id="ticket-a",
        tool="network_action",
        description="restart network",
    )
    action_b = PendingAction(
        action_id="action-b",
        ticket_id="ticket-b",
        tool="network_action",
        description="restart network",
    )
    memory.set_summary("ticket-a", "summary A")
    memory.set_summary("ticket-b", "summary B")
    memory.add("ticket-b", Message("user", "conversation B"))
    memory.set_pending(action_a)
    memory.set_pending(action_b)

    assert memory.get_summary("ticket-a") == "summary A"
    assert memory.get_summary("missing") == ""
    assert memory.pop_pending("action-a") == action_a
    assert memory.pop_pending("action-a") is None

    memory.clear("ticket-b")

    assert memory.get_summary("ticket-b") == ""
    assert memory.history("ticket-b") == []
    assert memory.active_tickets() == 0
    assert memory.pop_pending("action-b") is None
    assert memory.get_summary("ticket-a") == "summary A"


def test_short_term_rejects_negative_window() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        ShortTermMemory(window_turns=-1)


def test_long_term_creates_schema_persists_and_returns_recent_oldest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "assistant.db"
    memory = LongTermMemory(str(db_path))
    memory.save_turn("ticket-a", "user", "first", "network_issue")
    memory.save_turn("ticket-a", "assistant", "second")
    memory.save_turn("ticket-a", "user", "third", "software_issue")
    memory.save_turn("ticket-b", "user", "separate")

    recent = memory.recent("ticket-a", limit=2)

    assert db_path.exists()
    assert [turn["content"] for turn in recent] == ["second", "third"]
    assert recent[0]["role"] == "assistant"
    assert recent[0]["category"] is None
    assert recent[0]["created_at"]
    assert set(recent[0]) == {"role", "content", "category", "created_at"}
    assert memory.recent("ticket-a", limit=0) == []
    assert memory.recent("ticket-a", limit=-1) == []


def test_long_term_parameters_keep_ticket_values_as_data(tmp_path: Path) -> None:
    memory = LongTermMemory(str(tmp_path / "assistant.db"))
    unusual_ticket_id = "ticket-a' OR 1=1 --"
    memory.save_turn(unusual_ticket_id, "user", "safe row")
    memory.save_turn("ticket-b", "user", "other row")

    assert [row["content"] for row in memory.recent(unusual_ticket_id)] == ["safe row"]


def test_long_term_delete_ticket_removes_only_its_turns(tmp_path: Path) -> None:
    memory = LongTermMemory(str(tmp_path / "assistant.db"))
    memory.save_turn("ticket-a", "user", "delete me")
    memory.save_turn("ticket-b", "user", "keep me")

    memory.delete_ticket("ticket-a")

    assert memory.recent("ticket-a") == []
    assert [row["content"] for row in memory.recent("ticket-b")] == ["keep me"]

