"""Spawn hooks — PreSpawn and PostSpawn hooks for ClawTeam lifecycle events.

Mirrors the PreToolUse/PostToolUse pattern but at the agent spawn level:
- PreSpawn: Check drift, validate prerequisites before spawning agent
- PostSpawn: Run oracle evaluation, emit completion events after agent finishes
"""

from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class SpawnHookResult:
    """Result from a spawn hook."""
    abort: bool = False
    reason: str = ""
    metadata: dict[str, Any] | None = None


@dataclass
class SpawnContext:
    """Context passed to spawn hooks."""
    agent_name: str
    agent_id: str
    agent_type: str
    team_name: str
    session_id: str
    prompt: str
    workspace: str
    profile: dict[str, Any] | None = None


class SpawnHook(ABC):
    """Base class for spawn lifecycle hooks."""

    @abstractmethod
    async def pre_spawn(self, context: SpawnContext) -> SpawnHookResult:
        """Called before agent is spawned. Return abort=True to prevent spawn."""

    @abstractmethod
    async def post_spawn(self, context: SpawnContext, result: Any = None) -> None:
        """Called after agent completes. Non-blocking — exceptions are swallowed."""


class DriftCheckHook(SpawnHook):
    """Query AIE drift score before spawning. Abort if threshold exceeded.

    Uses the AIE drift scan CLI or direct index query to check whether
    the session has drifted beyond the agent's configured threshold.
    """

    def __init__(self, threshold: float = 0.7, socket_path: str | None = None):
        self.threshold = threshold
        self.socket_path = socket_path or os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")

    async def pre_spawn(self, context: SpawnContext) -> SpawnHookResult:
        """Check drift score. Abort if above threshold."""
        # Get drift threshold from profile if available
        threshold = context.profile.get("drift_threshold", self.threshold) if context.profile else self.threshold

        try:
            drift_score = await self._query_drift(context.session_id)
            if drift_score > threshold:
                return SpawnHookResult(
                    abort=True,
                    reason=f"Drift score {drift_score:.2f} exceeds threshold {threshold:.2f}",
                    metadata={"drift_score": drift_score, "threshold": threshold},
                )
            return SpawnHookResult(
                abort=False,
                metadata={"drift_score": drift_score},
            )
        except Exception as e:
            # If drift check fails, allow spawn but log the failure
            return SpawnHookResult(
                abort=False,
                reason=f"Drift check failed: {e}",
            )

    async def post_spawn(self, context: SpawnContext, result: Any = None) -> None:
        """No post-spawn action for drift check."""
        pass

    async def _query_drift(self, session_id: str) -> float:
        """Query AIE for current drift score."""
        import asyncio

        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            request = json.dumps({
                "jsonrpc": "2.0",
                "method": "drift_score",
                "params": {"session_id": session_id},
                "id": 1,
            }).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            response_bytes = await asyncio.wait_for(reader.readline(), timeout=5)
            writer.close()
            await writer.wait_closed()

            if response_bytes:
                response = json.loads(response_bytes.decode())
                return response.get("result", {}).get("score", 0.0)
        except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError):
            pass
        return 0.0


class OracleEvalHook(SpawnHook):
    """Run oracle evaluation after agent task completion.

    Queries the AIE oracle engine to evaluate whether the agent's actions
    met the defined standards for the task type.
    """

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")

    async def pre_spawn(self, context: SpawnContext) -> SpawnHookResult:
        """No pre-spawn action for oracle eval."""
        return SpawnHookResult(abort=False)

    async def post_spawn(self, context: SpawnContext, result: Any = None) -> None:
        """Run oracle evaluation and emit result event."""
        try:
            oracle_result = await self._run_oracle(context.session_id)
            await self._emit_oracle_event(context, oracle_result)
        except Exception:
            pass  # Never let post-spawn hooks fail the workflow

    async def _run_oracle(self, session_id: str) -> dict:
        """Query AIE oracle for session evaluation."""
        import asyncio

        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            request = json.dumps({
                "jsonrpc": "2.0",
                "method": "oracle_eval",
                "params": {"session_id": session_id},
                "id": 2,
            }).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            response_bytes = await asyncio.wait_for(reader.readline(), timeout=10)
            writer.close()
            await writer.wait_closed()

            if response_bytes:
                response = json.loads(response_bytes.decode())
                return response.get("result", {})
        except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError):
            pass
        return {"status": "skipped", "reason": "oracle_unavailable"}

    async def _emit_oracle_event(self, context: SpawnContext, oracle_result: dict) -> None:
        """Emit oracle evaluation event to AIE logger."""
        import asyncio

        event = {
            "schema_version": "1.0",
            "event_id": str(uuid.uuid4()),
            "event_type": "oracle_evaluation",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": context.agent_id,
            "session_id": context.session_id,
            "interaction_context": {
                "channel": "spawn_hook",
                "team": context.team_name,
                "agent_type": context.agent_type,
            },
            "oracle_result": oracle_result,
        }

        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            request = json.dumps({
                "jsonrpc": "2.0",
                "method": "emit",
                "params": {"event": event},
                "id": 3,
            }).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            await asyncio.wait_for(reader.readline(), timeout=3)
            writer.close()
            await writer.wait_closed()
        except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError):
            pass


class SessionLogHook(SpawnHook):
    """Log spawn events to ClawTeam session store.

    Writes pre/post spawn events to the session JSON file so ClawTeam's
    session persistence can track what happened.
    """

    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or os.environ.get(
            "CLAWTEAM_DATA_DIR",
            str(Path.home() / ".clawteam"),
        )

    async def pre_spawn(self, context: SpawnContext) -> SpawnHookResult:
        """Record spawn start in session log."""
        try:
            session_path = Path(self.data_dir) / "sessions" / context.team_name / f"{context.agent_name}.json"
            session_path.parent.mkdir(parents=True, exist_ok=True)

            session_data = {
                "agentName": context.agent_name,
                "teamName": context.team_name,
                "sessionId": context.session_id,
                "savedAt": datetime.now(timezone.utc).isoformat(),
                "state": {
                    "status": "spawning",
                    "prompt_preview": context.prompt[:200],
                    "agent_type": context.agent_type,
                },
            }
            tmp = session_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(session_data, indent=2), encoding="utf-8")
            tmp.rename(session_path)
        except Exception:
            pass

        return SpawnHookResult(abort=False)

    async def post_spawn(self, context: SpawnContext, result: Any = None) -> None:
        """Record spawn completion in session log."""
        try:
            session_path = Path(self.data_dir) / "sessions" / context.team_name / f"{context.agent_name}.json"
            if not session_path.exists():
                return

            data = json.loads(session_path.read_text(encoding="utf-8"))
            data["state"]["status"] = "completed"
            data["savedAt"] = datetime.now(timezone.utc).isoformat()

            if result and hasattr(result, "tool_calls"):
                data["state"]["tool_calls"] = result.tool_calls
                data["state"]["duration_ms"] = result.duration_ms

            tmp = session_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.rename(session_path)
        except Exception:
            pass


from pathlib import Path
