# tool_executor.py

import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class ToolResult:
    tool_name: str
    output: str
    exit_code: int
    duration_ms: int
    denied: bool = False
    error: str | None = None

class ToolExecutor:
    """Async tool executor — executes tools and returns results."""

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())

    async def execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        start = time.monotonic()
        try:
            if tool_name == "bash":
                result = await self._bash(tool_input)
            elif tool_name == "file_read":
                result = await self._file_read(tool_input)
            elif tool_name == "file_write":
                result = await self._file_write(tool_input)
            elif tool_name == "glob":
                result = await self._glob(tool_input)
            elif tool_name == "grep":
                result = await self._grep(tool_input)
            else:
                result = ToolResult(tool_name=tool_name, output="", exit_code=1, duration_ms=0, error=f"Unknown tool: {tool_name}")
        except Exception as e:
            result = ToolResult(tool_name=tool_name, output="", exit_code=1, duration_ms=0, error=str(e))
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _bash(self, tool_input: dict) -> ToolResult:
        cmd = tool_input.get("command", "")
        cwd = str(self.workspace_root)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode() + stderr.decode()
        return ToolResult(tool_name="bash", output=output, exit_code=proc.returncode, duration_ms=0)

    async def _file_read(self, tool_input: dict) -> ToolResult:
        path = self.workspace_root / tool_input.get("path", "")
        try:
            content = path.read_text()
            return ToolResult(tool_name="file_read", output=content, exit_code=0, duration_ms=0)
        except FileNotFoundError:
            return ToolResult(tool_name="file_read", output=f"File not found: {path}", exit_code=1, duration_ms=0)
        except Exception as e:
            return ToolResult(tool_name="file_read", output=str(e), exit_code=1, duration_ms=0)

    async def _file_write(self, tool_input: dict) -> ToolResult:
        path = self.workspace_root / tool_input.get("path", "")
        content = tool_input.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return ToolResult(tool_name="file_write", output=f"Wrote {len(content)} bytes to {path}", exit_code=0, duration_ms=0)
        except Exception as e:
            return ToolResult(tool_name="file_write", output=str(e), exit_code=1, duration_ms=0)

    async def _glob(self, tool_input: dict) -> ToolResult:
        import fnmatch
        pattern = tool_input.get("pattern", "*")
        root = self.workspace_root
        matches = [str(p.relative_to(root)) for p in root.rglob("*") if fnmatch.fnmatch(p.name, pattern) or fnmatch.fnmatch(str(p.relative_to(root)), pattern)]
        output = "\n".join(matches[:100])  # cap at 100
        return ToolResult(tool_name="glob", output=output, exit_code=0, duration_ms=0)

    async def _grep(self, tool_input: dict) -> ToolResult:
        import fnmatch
        pattern = tool_input.get("pattern", "")
        path = self.workspace_root / tool_input.get("path", "")
        try:
            matches = []
            for line_num, line in enumerate(path.read_text().splitlines(), 1):
                if pattern in line:
                    matches.append(f"{line_num}: {line.rstrip()}")
            output = "\n".join(matches[:50])
            return ToolResult(tool_name="grep", output=output, exit_code=0, duration_ms=0)
        except Exception as e:
            return ToolResult(tool_name="grep", output=str(e), exit_code=1, duration_ms=0)
