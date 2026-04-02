# aie_emitter.py
import json
import socket
import uuid
from datetime import datetime, timezone
from ..sanitiser import sanitise
from .base import ToolHook

class AIEEventEmitter(ToolHook):
    """Emits structured tool_call events to the AIE logger."""

    def __init__(self, socket_path: str = "/tmp/ailogger.sock", session_id: str | None = None):
        self.socket_path = socket_path
        self.session_id = session_id or "claw-aie-session"
        self.event_count = 0

    def _send_jsonrpc(self, method: str, params: dict) -> bool:
        """Send JSON-RPC 2.0 request over Unix socket."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(self.socket_path)
            request = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": self.event_count})
            sock.sendall((request + "\n").encode())
            sock.close()
            return True
        except Exception:
            return False

    def _build_event(self, event_type_suffix: str, tool_name: str, tool_input: dict, output: str = "", status: str = "pending") -> dict:
        self.event_count += 1
        return {
            "schema_version": "1.0",
            "event_id": str(uuid.uuid4()),
            "event_type": "tool_call",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": "claw-aie",
            "session_id": self.session_id,
            "interaction_context": {
                "channel": "terminal",
                "workspace_path": str(__import__('pathlib').Path.cwd()),
                "parent_event_id": None
            },
            "tool": {
                "name": tool_name,
                "namespace": "claw-aie",
                "arguments": sanitise(tool_input),
                "argument_schema": None
            },
            "trigger": {
                "type": "explicit_request",
                "triggered_by_event_id": None
            },
            "outcome": {
                "status": status,
                "duration_ms": 0,
                "error_message": None,
                "output_summary": output[:500] if output else None
            }
        }

    async def pre_tool_use(self, tool_name: str, tool_input: dict) -> None:
        event = self._build_event("pre", tool_name, tool_input, status="pending")
        self._send_jsonrpc("emit", {"event": event})

    async def post_tool_use(self, tool_name: str, tool_input: dict, output: str) -> None:
        status = "error" if output.startswith("Error:") or "not found" in output.lower() else "success"
        event = self._build_event("post", tool_name, tool_input, output, status=status)
        self._send_jsonrpc("emit", {"event": event})
