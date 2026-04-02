# base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class HookResult:
    allowed: bool = True
    denied: bool = False
    denial_message: str | None = None
    messages: list[str] = field(default_factory=list)

class ToolHook(ABC):
    """Base class for tool hooks."""

    @abstractmethod
    async def pre_tool_use(self, tool_name: str, tool_input: dict) -> HookResult | None:
        """Return None = allow. Return HookResult to override."""
        return None

    @abstractmethod
    async def post_tool_use(self, tool_name: str, tool_input: dict, output: str) -> None:
        """Post-execution. Cannot block."""
        pass
