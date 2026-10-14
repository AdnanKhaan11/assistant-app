"""The governance layer is complete, so these tests pass as soon as you create the project."""
import asyncio

from backend.tools.base import RiskLevel, Tool, ToolResult
from backend.tools.registry import ToolRegistry


class DummyAction(Tool):
    name, description, risk = "dummy_action", "changes state", RiskLevel.ACTION

    def __init__(self) -> None:
        self.ran = 0

    async def run(self, **_) -> ToolResult:
        self.ran += 1
        return ToolResult("ok", "done")


class SlowTool(Tool):
    name, description, risk = "slow", "never finishes in time", RiskLevel.INFO

    async def run(self, **_) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult("ok", "late")


class CrashTool(Tool):
    name, description, risk = "crash", "raises", RiskLevel.INFO

    async def run(self, **_) -> ToolResult:
        raise RuntimeError("boom")


async def test_action_is_blocked_without_confirmation(telemetry_capture):
    registry, tool = ToolRegistry(), DummyAction()
    registry.register(tool)
    result = await registry.execute("dummy_action")
    assert result.status == "error" and result.category == "confirmation_required"
    assert tool.ran == 0  # the tool body NEVER ran


async def test_action_runs_when_confirmed(telemetry_capture):
    registry, tool = ToolRegistry(), DummyAction()
    registry.register(tool)
    assert (await registry.execute("dummy_action", confirmed=True)).ok
    assert tool.ran == 1


async def test_timeout_returns_retryable_error(telemetry_capture):
    registry = ToolRegistry()
    registry.register(SlowTool())
    result = await registry.execute("slow", timeout_s=0.05)
    assert result.category == "transient_timeout" and result.retryable


async def test_crash_is_contained(telemetry_capture):
    registry = ToolRegistry()
    registry.register(CrashTool())
    result = await registry.execute("crash")
    assert result.category == "tool_error"
    assert "assistant.tool.calls" in telemetry_capture.metric_names()
