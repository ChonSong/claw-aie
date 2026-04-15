"""Agent harness — wraps an agent CLI subprocess with AIE instrumentation.

The Harness launches an agent CLI (claude, codex, gemini, etc.), parses its
output for structured tool-call markers, and routes each detected tool call
through the claw-aie hook pipeline (pre/post hooks, AIE event emission).

This is the bridge between ClawTeam's spawn system and claw-aie's observability.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hooks.runner import HookRunner
from .tool_executor import ToolExecutor, ToolResult


@dataclass
class HarnessResult:
    """Result from a complete harness run."""
    exit_code: int
    output: str
    tool_calls: int = 0
    duration_ms: int = 0
    session_id: str = ""
    error: str | None = None


@dataclass
class ParsedToolCall:
    """A tool call parsed from agent CLI output."""
    tool_name: str
    tool_input: dict
    line_number: int = 0
    raw_line: str = ""


# Patterns for detecting tool calls in agent CLI output.
# Different CLIs emit different formats — we handle the common ones.
TOOL_CALL_PATTERNS = [
    # Claude Code format: <tool_call tool="name">input</tool_call
    re.compile(r'<tool_call\s+tool="([^"]+)"\s*>(.*?)</tool_call', re.DOTALL),
    # Generic JSON format: {"tool": "name", "input": {...}}
    re.compile(r'\{"tool":\s*"([^"]+)",\s*"input":\s*(\{[^}]*\})\}'),
    # Codex format: Tool(name, input)
    re.compile(r'Tool\((\w+),\s*(\{[^}]*\})\)'),
    # Fallback: tool_call:name(input_json)
    re.compile(r'tool_call:(\w+)\((\{[^}]*\})\)'),
]


class Harness:
    """Wraps an agent CLI subprocess with AIE hook instrumentation.

    Usage:
        harness = Harness(workspace_root="/path/to/repo", session_id="abc")
        harness.register_hooks(my_hook)
        result = await harness.run_agent(
            command=["claude", "--print", "-p"],
            prompt="Fix the login bug",
        )
    """

    def __init__(
        self,
        workspace_root: str | None = None,
        session_id: str | None = None,
        agent_id: str = "harness",
    ):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.agent_id = agent_id

        # Build executor + hook runner
        self.executor = ToolExecutor(workspace_root=str(self.workspace_root))
        self.hooks = HookRunner(self.executor)

        # Tracking
        self._tool_calls: list[ParsedToolCall] = []
        self._start_time: float = 0

    def register_hook(self, hook) -> None:
        """Register a pre/post hook on the harness executor."""
        # Hooks implement pre_tool_use / post_tool_use
        if hasattr(hook, 'pre_tool_use'):
            self.hooks.register_pre(hook)
        if hasattr(hook, 'post_tool_use'):
            self.hooks.register_post(hook)

    def register_browser_tools(self) -> None:
        """Register browser tools for visual review workflows."""
        self.executor.register_browser_tools()

    def register_tool(self, name: str, handler) -> None:
        """Register a custom tool handler."""
        self.executor.register_tool(name, handler)

    async def run_agent(
        self,
        command: list[str],
        prompt: str,
        env: dict[str, str] | None = None,
        timeout: int = 300,
        capture_output: bool = True,
    ) -> HarnessResult:
        """Launch agent CLI, parse output for tool calls, emit AIE events.

        Args:
            command: Agent CLI command (e.g. ["claude", "--print"])
            prompt: Task prompt to send to the agent
            env: Additional env vars for the subprocess
            timeout: Max seconds to wait for agent completion
            capture_output: Whether to capture stdout/stderr

        Returns:
            HarnessResult with exit code, output, and tool call count
        """
        self._start_time = time.monotonic()
        self._tool_calls.clear()

        # Build subprocess environment
        spawn_env = os.environ.copy()
        spawn_env.update({
            "CLAW_AIE_SESSION_ID": self.session_id,
            "CLAW_AIE_AGENT_ID": self.agent_id,
            "CLAW_AIE_WORKSPACE": str(self.workspace_root),
        })
        if env:
            spawn_env.update(env)

        # Build full command with prompt
        final_command = list(command)
        if prompt:
            final_command.append(prompt)

        # Emit session start event
        await self._emit_session_event("session_start", {
            "command": command,
            "prompt_preview": prompt[:200],
        })

        try:
            proc = await asyncio.create_subprocess_exec(
                *final_command,
                stdout=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
                env=spawn_env,
                cwd=str(self.workspace_root),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            output = ""
            if stdout:
                output += stdout.decode(errors="replace")
            if stderr:
                output += stderr.decode(errors="replace")

            # Parse tool calls from output
            await self._parse_and_execute_tool_calls(output)

            duration_ms = int((time.monotonic() - self._start_time) * 1000)

            # Emit session end event
            await self._emit_session_event("session_end", {
                "exit_code": proc.returncode,
                "tool_calls": len(self._tool_calls),
                "duration_ms": duration_ms,
            })

            return HarnessResult(
                exit_code=proc.returncode or 0,
                output=output,
                tool_calls=len(self._tool_calls),
                duration_ms=duration_ms,
                session_id=self.session_id,
            )

        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - self._start_time) * 1000)
            await self._emit_session_event("session_timeout", {
                "timeout": timeout,
                "tool_calls": len(self._tool_calls),
            })
            return HarnessResult(
                exit_code=-1,
                output=f"Agent timed out after {timeout}s",
                tool_calls=len(self._tool_calls),
                duration_ms=duration_ms,
                session_id=self.session_id,
                error="timeout",
            )

        except Exception as e:
            duration_ms = int((time.monotonic() - self._start_time) * 1000)
            return HarnessResult(
                exit_code=-1,
                output=str(e),
                tool_calls=len(self._tool_calls),
                duration_ms=duration_ms,
                session_id=self.session_id,
                error=str(e),
            )

    async def run_tool_directly(
        self,
        tool_name: str,
        tool_input: dict,
    ) -> ToolResult:
        """Execute a single tool call through the harness (no agent CLI).

        Useful for programmatic workflows like browser review where
        the orchestrator directly invokes tools.
        """
        return await self.executor.execute(tool_name, tool_input)

    async def _parse_and_execute_tool_calls(self, output: str) -> None:
        """Parse agent output for tool calls and route through hook pipeline."""
        for line_num, line in enumerate(output.splitlines(), 1):
            for pattern in TOOL_CALL_PATTERNS:
                match = pattern.search(line)
                if match:
                    tool_name = match.group(1)
                    try:
                        tool_input = json.loads(match.group(2))
                    except (json.JSONDecodeError, IndexError):
                        tool_input = {}

                    parsed = ParsedToolCall(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        line_number=line_num,
                        raw_line=line,
                    )
                    self._tool_calls.append(parsed)

                    # Execute through the hook pipeline
                    # This triggers PreToolUse → execute → PostToolUse → AIE emit
                    await self.executor.execute(tool_name, tool_input)
                    break  # one match per line is enough

    async def _emit_session_event(self, event_type: str, payload: dict) -> None:
        """Emit a session lifecycle event to AIE logger."""
        try:
            import json as _json
            from .session import get_session

            _, agent_id = get_session()

            event = {
                "schema_version": "1.0",
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "timestamp": _now_iso(),
                "agent_id": agent_id or self.agent_id,
                "session_id": self.session_id,
                "interaction_context": {
                    "channel": "harness",
                    "workspace_path": str(self.workspace_root),
                },
                **payload,
            }

            # Try to emit via AIE logger socket
            await self._emit_to_logger(event)
        except Exception:
            pass  # Never let event emission crash the harness

    async def _emit_to_logger(self, event: dict) -> None:
        """Send event to AIE logger via Unix socket (JSON-RPC)."""
        socket_path = os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)
            request = json.dumps({
                "jsonrpc": "2.0",
                "method": "emit",
                "params": {"event": event},
                "id": 0,
            }).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            await asyncio.wait_for(reader.readline(), timeout=3)
            writer.close()
            await writer.wait_closed()
        except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError):
            pass  # Logger not running — events silently dropped

    @property
    def tool_calls(self) -> list[ParsedToolCall]:
        """Return all parsed tool calls from the last run."""
        return list(self._tool_calls)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
