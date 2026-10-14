"""Tool contract (book ch.9): name, purpose, inputs, output, errors, side effects, RISK LEVEL."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class RiskLevel(str, Enum):
    INFO = "info"              # read-only lookups
    DIAGNOSTIC = "diagnostic"  # inspects state, changes nothing
    ACTION = "action"          # changes the world -> ALWAYS needs human confirmation


@dataclass
class ToolResult:
    status: Literal["ok", "error"]
    details: str
    category: str | None = None   # error category: transient_timeout | tool_error | confirmation_required ...
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class Tool(ABC):
    name: str
    description: str
    risk: RiskLevel

    @property
    def requires_confirmation(self) -> bool:
        return self.risk is RiskLevel.ACTION

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        """Do the work. Raise nothing for expected failures - return ToolResult(status="error", ...)."""
