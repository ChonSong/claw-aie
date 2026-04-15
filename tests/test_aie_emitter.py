"""TDD tests for AIEEventEmitter — Phase C."""
import pytest
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from aie_integration.hooks.aie_emitter import AIEEventEmitter
from aie_integration.hooks.base import HookResult
from aie_integration.session import set_session


def _extract_event(mock_call):
    """Extract event dict from mock call args (handles both positional and kwargs)."""
    args, kwargs = mock_call
    if args:
        # Called as _send_jsonrpc_async("emit", {"event": event})
        method = args[0]
        params = args[1]
        return params["event"]
    # Fallback to kwargs if used
    return kwargs.get("params", {}).get("event", {})


class TestAIEEventEmitterPreToolUse:
    """Test pre_tool_use emits correct event structure."""

    @pytest.mark.asyncio
    async def test_pre_emits_pending_status(self):
        """pre_tool_use should emit event with status='pending'."""
        set_session("test-session-123", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-123")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        result = await emitter.pre_tool_use("bash", {"command": "echo hello"})

        # pre hooks always return None (never block)
        assert result is None
        # Should have sent the event
        emitter._send_jsonrpc_async.assert_called_once()
        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["outcome"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_pre_emits_correct_schema(self):
        """pre_tool_use should emit event with correct schema fields."""
        set_session("test-session-456", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-456")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("file_read", {"path": "/tmp/test.txt"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)

        # Required schema fields
        assert "schema_version" in event
        assert event["schema_version"] == "1.0"
        assert "event_id" in event
        assert "event_type" in event
        assert event["event_type"] == "tool_call"
        assert "timestamp" in event
        assert "agent_id" in event
        assert "session_id" in event
        assert "interaction_context" in event
        assert "tool" in event
        assert "trigger" in event
        assert "outcome" in event

    @pytest.mark.asyncio
    async def test_pre_tool_fields(self):
        """pre_tool_use should emit correct tool info."""
        set_session("test-session-789", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-789")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("glob", {"pattern": "*.py"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)

        assert event["tool"]["name"] == "glob"
        assert event["tool"]["namespace"] == "claw-aie"
        assert event["tool"]["arguments"] == {"pattern": "*.py"}
        assert event["tool"]["argument_schema"] is None

    @pytest.mark.asyncio
    async def test_pre_trigger_fields(self):
        """pre_tool_use should emit correct trigger info."""
        set_session("test-session-abc", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-abc")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("bash", {"command": "ls"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)

        assert event["trigger"]["type"] == "explicit_request"
        assert event["trigger"]["triggered_by_event_id"] is None


class TestAIEEventEmitterPostToolUse:
    """Test post_tool_use emits correct event structure."""

    @pytest.mark.asyncio
    async def test_post_emits_success_on_normal_output(self):
        """post_tool_use should emit success when output is normal."""
        set_session("test-session-post", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-post")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.post_tool_use("bash", {"command": "echo hello"}, "hello\n")

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["outcome"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_post_emits_error_on_error_prefix(self):
        """post_tool_use should emit error when output starts with 'Error:'."""
        set_session("test-session-err", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-err")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.post_tool_use("bash", {"command": "cat /nonexistent"}, "Error: file not found")

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["outcome"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_post_emits_error_on_not_found(self):
        """post_tool_use should emit error when output contains 'not found'."""
        set_session("test-session-nf", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-nf")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.post_tool_use("file_read", {"path": "/missing.txt"}, "File /missing.txt not found")

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["outcome"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_post_includes_duration_ms(self):
        """post_tool_use should include duration_ms in outcome."""
        set_session("test-session-dur", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-dur")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.post_tool_use("bash", {"command": "sleep 0.1"}, "done", duration_ms=150)

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["outcome"]["duration_ms"] == 150


class TestAIEEventEmitterSanitisation:
    """Test that sensitive fields are sanitised."""

    @pytest.mark.asyncio
    async def test_sanitises_password_field(self):
        """Password field should be redacted."""
        set_session("test-session-san", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-san")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("bash", {"command": "login", "PASSWORD": "secret123"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["tool"]["arguments"]["PASSWORD"] == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_sanitises_api_key_field(self):
        """API_KEY field should be redacted."""
        set_session("test-session-key", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-key")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("api_call", {"endpoint": "/data", "API_KEY": "sk-12345"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["tool"]["arguments"]["API_KEY"] == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_sanitises_token_field(self):
        """TOKEN field should be redacted."""
        set_session("test-session-token", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-token")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("http_request", {"url": "http://api", "TOKEN": "bearer xyz"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["tool"]["arguments"]["TOKEN"] == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_sanitises_secret_field(self):
        """SECRET field should be redacted."""
        set_session("test-session-secret", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-secret")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("auth", {"user": "admin", "SECRET": "mysecret"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["tool"]["arguments"]["SECRET"] == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_sanitises_authorization_field(self):
        """AUTHORIZATION field should be redacted."""
        set_session("test-session-authz", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-authz")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("curl", {"url": "http://api", "AUTHORIZATION": "Basic abc"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["tool"]["arguments"]["AUTHORIZATION"] == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_non_sensitive_fields_preserved(self):
        """Non-sensitive fields should not be redacted."""
        set_session("test-session-safe", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-safe")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("bash", {"command": "ls", "path": "/tmp", "verbose": True})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["tool"]["arguments"]["command"] == "ls"
        assert event["tool"]["arguments"]["path"] == "/tmp"
        assert event["tool"]["arguments"]["verbose"] is True


class TestAIEEventEmitterOutputTruncation:
    """Test output truncation to 500 chars."""

    @pytest.mark.asyncio
    async def test_truncates_long_output(self):
        """Output over 500 chars should be truncated."""
        set_session("test-session-long", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-long")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        long_output = "x" * 600
        await emitter.post_tool_use("bash", {"command": "cat file"}, long_output)

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert len(event["outcome"]["output_summary"]) == 500

    @pytest.mark.asyncio
    async def test_preserves_short_output(self):
        """Output under 500 chars should not be truncated."""
        set_session("test-session-short", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-short")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        short_output = "hello world"
        await emitter.post_tool_use("bash", {"command": "echo hello"}, short_output)

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["outcome"]["output_summary"] == "hello world"

    @pytest.mark.asyncio
    async def test_null_output_when_empty(self):
        """Empty output should result in null output_summary."""
        set_session("test-session-empty", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-empty")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.post_tool_use("bash", {"command": "true"}, "")

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["outcome"]["output_summary"] is None


class TestAIEEventEmitterEventId:
    """Test event ID generation."""

    @pytest.mark.asyncio
    async def test_generates_unique_event_ids(self):
        """Each event should have a unique event_id."""
        set_session("test-session-uid", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-uid")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("bash", {"command": "echo 1"})
        await emitter.pre_tool_use("bash", {"command": "echo 2"})
        await emitter.post_tool_use("bash", {"command": "echo 3"}, "output")

        calls = emitter._send_jsonrpc_async.call_args_list
        event1 = _extract_event(calls[0])
        event2 = _extract_event(calls[1])
        event3 = _extract_event(calls[2])

        assert event1["event_id"] != event2["event_id"]
        assert event2["event_id"] != event3["event_id"]
        assert event1["event_id"] != event3["event_id"]


class TestAIEEventEmitterTimestamp:
    """Test timestamp generation."""

    @pytest.mark.asyncio
    async def test_timestamp_is_iso8601(self):
        """Timestamp should be in ISO-8601 format."""
        set_session("test-session-ts", "claw-aie")
        emitter = AIEEventEmitter(session_id="test-session-ts")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("bash", {"command": "date"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        # ISO-8601 format should contain 'T' for separator
        assert "T" in event["timestamp"]
        # Either ends with Z (UTC) or +00:00 (UTC offset)
        assert event["timestamp"].endswith("Z") or event["timestamp"].endswith("+00:00")


class TestAIEEventEmitterSessionFallback:
    """Test session ID fallback behavior."""

    @pytest.mark.asyncio
    async def test_uses_static_session_id_when_no_context(self):
        """Should use static session_id when no context is set."""
        emitter = AIEEventEmitter(session_id="static-session-id")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("bash", {"command": "echo"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        assert event["session_id"] == "static-session-id"

    @pytest.mark.asyncio
    async def test_uses_context_session_id_when_available(self):
        """Should use session from context when available."""
        set_session("context-session-id", "my-agent")
        emitter = AIEEventEmitter(session_id="static-session-id")
        emitter._send_jsonrpc_async = AsyncMock(return_value={"result": {"status": "ok"}})

        await emitter.pre_tool_use("bash", {"command": "echo"})

        event = _extract_event(emitter._send_jsonrpc_async.call_args)
        # Context session takes precedence over static
        assert event["session_id"] == "context-session-id"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
