# runner.py
from dataclasses import dataclass
from typing import TYPE_CHECKING
from .base import HookResult, ToolHook

if TYPE_CHECKING:
    from ..tool_executor import ToolExecutor

@dataclass
class HookConfig:
    name: str
    enabled: bool = True
    tools: list[str] | None = None  # None = all tools

class HookRunner:
    """PreToolUse + PostToolUse execution pipeline."""

    def __init__(self, executor: "ToolExecutor"):
        self.executor = executor
        self.pre_hooks: list[ToolHook] = []
        self.post_hooks: list[ToolHook] = []

    def register_pre(self, hook: ToolHook) -> None:
        self.pre_hooks.append(hook)

    def register_post(self, hook: ToolHook) -> None:
        self.post_hooks.append(hook)

    async def run_pre_tool_use(self, tool_name: str, tool_input: dict) -> HookResult:
        """Run all pre hooks. First denial wins."""
        for hook in self.pre_hooks:
            result = await hook.pre_tool_use(tool_name, tool_input)
            if result is not None and result.denied:
                return result
        return HookResult(allowed=True)

    async def run_post_tool_use(self, tool_name: str, tool_input: dict, output: str) -> None:
        """Run all post hooks. Non-blocking."""
        for hook in self.post_hooks:
            try:
                await hook.post_tool_use(tool_name, tool_input, output)
            except Exception:
                pass  # never let post hooks crash execution
