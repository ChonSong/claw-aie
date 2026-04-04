# aie_emitter.py
import asyncio
import json
import uuid
from datetime import datetime, timezone
from ..sanitiser import sanitise
from .base import HookResult, ToolHook


class AIEEventEmitter(ToolHook):
    """Emits structured tool_call events to the AIE logger via async Unix socket."""

    def __init__(self, socket_path: str | None = None, session_id: str | None = None):
        self.socket_path = socket_path or "/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/evaluator/data/ailogger.sock"
        self.session_id = session_id or "claw-aie-session"
        self.event_count = 0

    async def _send_jsonrpc_async(self, method: str, params: dict) -> dict | None:
        """Send JSON-RPC 2.0 request over Unix socket, read and return the response."""
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            request = json.dumps({
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": self.event_count,
            }).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            response_bytes = await asyncio.wait_for(reader.readline(), timeout=3)
            writer.close()
            await writer.wait_closed()
            if response_bytes:
                return json.loads(response_bytes.decode("utf-8"))
            return None
        except asyncio.TimeoutError:
            return {"error": "timeout"}
        except Exception as e:
            return {"error": str(e)}

    def _build_event(
        self,
        tool_name: str,
        tool_input: dict,
        output: str = "",
        status: str = "pending",
    ) -> dict:
        self.event_count += 1
        import pathlib
        return {
            "schema_version": "1.0",
            "event_id": str(uuid.uuid4()),
            "event_type": "tool_call",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": "claw-aie",
            "session_id": self.session_id,
            "interaction_context": {
                "channel": "claw-aie",
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
                "duration_ms": 0,
                "error_message": None,
                "output_summary": output[:500] if output else None,
            },
        }

    async def pre_tool_use(self, tool_name: str, tool_input: dict) -> HookResult | None:
        event = self._build_event(tool_name, tool_input, status="partial")
        await self._send_jsonrpc_async("emit", {"event": event})
        # pre hooks never block execution
        return None

    async def post_tool_use(self, tool_name: str, tool_input: dict, output: str) -> None:
        is_error = isinstance(output, str) and (
            output.startswith("Error:") or "not found" in output.lower()
        )
        status = "error" if is_error else "success"
        event = self._build_event(tool_name, tool_input, output, status=status)
        await self._send_jsonrpc_async("emit", {"event": event})
