# aie_emitter.py
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from ..sanitiser import sanitise
from ..session import get_session
from .base import HookResult, ToolHook


class AIEEventEmitter(ToolHook):
    """Emits structured tool_call events to the AIE logger via async Unix socket."""

    def __init__(self, socket_path: str | None = None, session_id: str | None = None):
        self.client = AIELoggerClient(socket_path)
        self._static_session_id = session_id  # fallback when no context
        self.event_count = 0

    def _build_event(
        self,
        tool_name: str,
        tool_input: dict,
        output: str = "",
        status: str = "pending",
        duration_ms: int = 0,
    ) -> dict:
        self.event_count += 1
        import pathlib

        session_id, agent_id = get_session()
        # Context session takes precedence; static session_id is fallback
        resolved_session_id = session_id if session_id != "default" else self._static_session_id
        resolved_agent_id = agent_id

        return {
            "schema_version": "1.0",
            "event_id": str(uuid.uuid4()),
            "event_type": "tool_call",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": resolved_agent_id,
            "session_id": resolved_session_id,
            "interaction_context": {
                "channel": "terminal",
                "workspace_path": str(pathlib.Path.cwd()),
                "parent_event_id": None,
            },
            "tool": {
                "name": tool_name,
                "namespace": "claw-aie",
                "arguments": sanitise(tool_input),
                "argument_schema": None,
            },
            "trigger": {
                "type": "explicit_request",
                "triggered_by_event_id": None,
            },
            "outcome": {
                "status": status,
                "duration_ms": duration_ms,
                "error_message": None,
                "output_summary": output[:500] if output else None,
            },
        }

    async def pre_tool_use(self, tool_name: str, tool_input: dict) -> HookResult | None:
        event = self._build_event(tool_name, tool_input, status="pending")
        await self.client.emit(event)
        # pre hooks never block execution
        return None

    async def post_tool_use(self, tool_name: str, tool_input: dict, output: str, duration_ms: int = 0) -> None:
        is_error = isinstance(output, str) and (
            output.startswith("Error:") or "not found" in output.lower()
        )
        status = "error" if is_error else "success"
        event = self._build_event(tool_name, tool_input, output, status=status, duration_ms=duration_ms)
        await self.client.emit(event)
