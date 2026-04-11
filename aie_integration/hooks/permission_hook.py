"""Permission hook - blocks destructive commands."""
import re
from typing import Any

from .base import HookResult, ToolHook


# Dangerous patterns for bash commands
DANGEROUS_BASH_PATTERNS = [
    r"rm\s+-rf",           # rm -rf anywhere
    r">\s*/dev/sd[a-z]",   # redirect to block device
    r"dd\s+if=",           # dd with input file
    r":\(\)\{",            # fork bomb
    r"mv\s+.*/\s+/$",      # move to root trap
    r">\s*/etc/",          # overwrite /etc
    r">\s*/sys/",          # write to /sys
    r"chmod\s+-R\s+777\s+/",  # chmod 777 entire system
]

# Protected system directories for file_write
PROTECTED_DIRECTORIES = [
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/sys",
]


class PermissionHook(ToolHook):
    """Blocks destructive and dangerous commands.

    Args:
        tools: List of tool names to apply this hook to.
               None = all tools.
    """

    def __init__(self, tools: list[str] | None = None):
        self.tools = tools
        self._dangerous_patterns = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_BASH_PATTERNS]

    def _is_applicable(self, tool_name: str) -> bool:
        """Check if this hook applies to the given tool."""
        if self.tools is None:
            return True
        return tool_name in self.tools

    async def pre_tool_use(self, tool_name: str, tool_input: dict) -> HookResult | None:
        """Check if the tool use should be allowed."""
        if not self._is_applicable(tool_name):
            return None

        if tool_name == "bash":
            return self._check_bash(tool_input)
        elif tool_name == "file_write":
            return self._check_file_write(tool_input)

        return None

    def _check_bash(self, tool_input: dict) -> HookResult | None:
        """Check if a bash command is safe."""
        command = tool_input.get("command", "")

        for pattern in self._dangerous_patterns:
            if pattern.search(command):
                return HookResult(
                    denied=True,
                    denial_message=f"Dangerous command pattern detected: {command[:50]}..."
                )

        return None

    def _check_file_write(self, tool_input: dict) -> HookResult | None:
        """Check if a file_write target is protected."""
        path = tool_input.get("path", "")

        # Normalize path
        if not path.startswith("/"):
            # Relative path - not a system directory
            return None

        # Check if path starts with any protected directory
        for protected in PROTECTED_DIRECTORIES:
            if path == protected or path.startswith(protected + "/"):
                return HookResult(
                    denied=True,
                    denial_message=f"Cannot write to protected path: {path}"
                )

        return None

    async def post_tool_use(self, tool_name: str, tool_input: dict, output: str) -> None:
        """Post-execution hook - nothing to do for permission hook."""
        pass