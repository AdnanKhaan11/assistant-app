from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from backend.tests.conftest import Capture
from backend.tools.base import RiskLevel
from backend.tools.issue_lookup import IssueLookupTool
from backend.tools.network_action import NetworkActionTool, _RESTARTED
from backend.tools.network_check import NetworkCheckTool
from backend.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    ("records", "ticket_id", "expected_status", "expected_category", "expected_details"),
    [
        ([{"id": "T-1", "details": "Wi-Fi drops"}], "T-1", "ok", None, "Wi-Fi drops"),
        ([{"id": "T-1", "details": "Wi-Fi drops"}], "T-2", "error", "not_found", "No ticket T-2"),
    ],
)
async def test_issue_lookup_returns_found_or_missing_result(
    tmp_path: Path,
    records: list[dict[str, str]],
    ticket_id: str,
    expected_status: str,
    expected_category: str | None,
    expected_details: str,
) -> None:
    tickets_path = tmp_path / "tickets.json"
    tickets_path.write_text(json.dumps(records), encoding="utf-8")
    tool = IssueLookupTool(str(tickets_path))

    result = await tool.run(ticket_id=ticket_id)

    assert result.status == expected_status
    assert result.category == expected_category
    assert result.details == expected_details


async def test_issue_lookup_loads_off_loop_and_caches_successful_read(tmp_path: Path) -> None:
    tickets_path = tmp_path / "tickets.json"
    tickets_path.write_text(json.dumps([{"id": "T-1", "details": "initial"}]), encoding="utf-8")
    tool = IssueLookupTool(str(tickets_path))
    caller_thread = threading.get_ident()
    load_threads: list[int] = []
    original_load = tool._load

    def tracked_load() -> dict[str, str]:
        load_threads.append(threading.get_ident())
        return original_load()

    tool._load = tracked_load
    first = await tool.run(ticket_id="T-1")
    tickets_path.write_text(json.dumps([{"id": "T-1", "details": "changed"}]), encoding="utf-8")
    second = await tool.run(ticket_id="T-1")

    assert first.details == second.details == "initial"
    assert len(load_threads) == 1
    assert load_threads[0] != caller_thread


@pytest.mark.parametrize("contents", ["{broken", json.dumps({"id": "T-1"}), json.dumps([{"id": 3, "details": "bad"}])])
async def test_issue_lookup_contains_invalid_ticket_data(tmp_path: Path, contents: str) -> None:
    tickets_path = tmp_path / "tickets.json"
    tickets_path.write_text(contents, encoding="utf-8")
    tool = IssueLookupTool(str(tickets_path))

    result = await tool.run(ticket_id="T-1")

    assert result.status == "error"
    assert result.category == "tool_error"
    assert result.details == "Ticket details are temporarily unavailable."


async def test_issue_lookup_contains_missing_file(tmp_path: Path) -> None:
    result = await IssueLookupTool(str(tmp_path / "missing.json")).run(ticket_id="T-1")

    assert result.status == "error"
    assert result.category == "tool_error"


async def test_network_check_returns_deterministic_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("backend.tools.network_check.asyncio.sleep", fake_sleep)
    tool = NetworkCheckTool()

    result = await tool.run()

    assert tool.risk is RiskLevel.DIAGNOSTIC
    assert delays == [0.3]
    assert result.status == "ok"
    assert result.details == "packet loss 7%, latency 180ms, DNS ok"


async def test_network_action_is_idempotent_and_registry_gated(
    monkeypatch: pytest.MonkeyPatch,
    telemetry_capture: Capture,
) -> None:
    ticket_id = "test-network-action"
    _RESTARTED.discard(ticket_id)
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("backend.tools.network_action.asyncio.sleep", fake_sleep)
    registry = ToolRegistry()
    action = NetworkActionTool()
    registry.register(action)

    blocked = await registry.execute("network_action", ticket_id=ticket_id)
    first = await registry.execute("network_action", confirmed=True, ticket_id=ticket_id)
    second = await registry.execute("network_action", confirmed=True, ticket_id=ticket_id)

    assert action.risk is RiskLevel.ACTION
    assert blocked.category == "confirmation_required"
    assert first.details == "Network service restarted."
    assert second.details == "Network service already restarted for this ticket - nothing to do."
    assert delays == [0.5]
    _RESTARTED.discard(ticket_id)

