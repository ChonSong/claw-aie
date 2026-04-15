"""Tests for harness, spawn backend, and spawn hooks."""

import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aie_integration.harness import Harness, HarnessResult, ParsedToolCall, TOOL_CALL_PATTERNS
from aie_integration.spawn_hooks import (
    SpawnContext,
    SpawnHookResult,
    DriftCheckHook,
    OracleEvalHook,
    SessionLogHook,
)


# ── Harness tests ──

def test_harness_creates_with_defaults():
    """Harness initializes with workspace, session, and agent."""
    harness = Harness(workspace_root="/tmp")
    assert harness.workspace_root == Path("/tmp")
    assert harness.session_id
    assert harness.agent_id == "harness"
    assert harness.executor is not None
    assert harness.hooks is not None


def test_harness_custom_session_and_agent():
    """Harness respects custom session_id and agent_id."""
    harness = Harness(session_id="test-123", agent_id="reviewer")
    assert harness.session_id == "test-123"
    assert harness.agent_id == "reviewer"


def test_register_browser_tools():
    """register_browser_tools adds browser tools to executor."""
    harness = Harness()
    harness.register_browser_tools()
    assert "browser_navigate" in harness.executor._custom_tools
    assert "browser_screenshot" in harness.executor._custom_tools
    assert "browser_click" in harness.executor._custom_tools


def test_register_custom_tool():
    """register_tool adds a custom tool handler."""
    harness = Harness()

    async def my_tool(inp):
        from aie_integration.tool_executor import ToolResult
        return ToolResult(tool_name="my_tool", output="ok", exit_code=0, duration_ms=0)

    harness.register_tool("my_tool", my_tool)
    assert "my_tool" in harness.executor._custom_tools


@pytest.mark.asyncio
async def test_run_tool_directly():
    """run_tool_directly executes through hook pipeline."""
    harness = Harness(workspace_root="/tmp")
    result = await harness.run_tool_directly("bash", {"command": "echo hello"})
    assert result.exit_code == 0
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_run_tool_directly_unknown():
    """run_tool_directly returns error for unknown tool."""
    harness = Harness(workspace_root="/tmp")
    result = await harness.run_tool_directly("nonexistent", {})
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_run_agent_success():
    """run_agent launches subprocess and captures output."""
    harness = Harness(workspace_root="/tmp", session_id="test-run")
    result = await harness.run_agent(
        command=["echo"],
        prompt="hello world",
        timeout=10,
    )
    assert result.exit_code == 0
    assert "hello world" in result.output
    assert result.session_id == "test-run"


@pytest.mark.asyncio
async def test_run_agent_timeout():
    """run_agent returns timeout error when agent exceeds timeout."""
    harness = Harness(workspace_root="/tmp", session_id="timeout-test")
    result = await harness.run_agent(
        command=["sleep", "10"],
        prompt="",
        timeout=1,
    )
    assert result.exit_code == -1
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_run_agent_tool_call_parsing():
    """run_agent parses JSON tool calls from output."""
    harness = Harness(workspace_root="/tmp", session_id="parse-test")

    # Use echo to simulate agent output with tool call
    result = await harness.run_agent(
        command=["echo", '{"tool": "bash", "input": {"command": "ls"}}'],
        prompt="",
        timeout=10,
    )
    assert result.exit_code == 0
    # The tool call should be parsed
    assert len(harness.tool_calls) >= 1
    assert harness.tool_calls[0].tool_name == "bash"


# ── Tool call pattern tests ──

def test_tool_call_patterns_json_format():
    """JSON format tool calls are detected."""
    line = '{"tool": "bash", "input": {"command": "ls"}}'
    matches = []
    for pattern in TOOL_CALL_PATTERNS:
        m = pattern.search(line)
        if m:
            matches.append(m)
    assert len(matches) >= 1
    assert matches[0].group(1) == "bash"


def test_tool_call_patterns_codex_format():
    """Codex format tool calls are detected."""
    line = 'Tool(bash, {"command": "ls"})'
    matches = []
    for pattern in TOOL_CALL_PATTERNS:
        m = pattern.search(line)
        if m:
            matches.append(m)
    assert len(matches) >= 1
    assert matches[0].group(1) == "bash"


def test_tool_call_patterns_fallback_format():
    """Fallback format tool calls are detected."""
    line = 'tool_call:bash({"command": "ls"})'
    matches = []
    for pattern in TOOL_CALL_PATTERNS:
        m = pattern.search(line)
        if m:
            matches.append(m)
    assert len(matches) >= 1
    assert matches[0].group(1) == "bash"


# ── Spawn context tests ──

def test_spawn_context():
    """SpawnContext holds spawn metadata."""
    ctx = SpawnContext(
        agent_name="reviewer",
        agent_id="abc123",
        agent_type="browser-review",
        team_name="my-team",
        session_id="sess-1",
        prompt="Review PR #42",
        workspace="/tmp/repo",
    )
    assert ctx.agent_name == "reviewer"
    assert ctx.agent_type == "browser-review"


def test_spawn_hook_result_defaults():
    """SpawnHookResult defaults to no abort."""
    result = SpawnHookResult()
    assert result.abort is False
    assert result.reason == ""
    assert result.metadata is None


def test_spawn_hook_result_abort():
    """SpawnHookResult can indicate abort."""
    result = SpawnHookResult(abort=True, reason="drift too high")
    assert result.abort is True
    assert "drift" in result.reason


# ── Drift check hook tests ──

@pytest.mark.asyncio
async def test_drift_check_hook_allows_when_no_logger():
    """DriftCheckHook allows spawn when AIE logger is not running."""
    hook = DriftCheckHook(threshold=0.7)
    ctx = SpawnContext(
        agent_name="test", agent_id="a1", agent_type="general",
        team_name="team", session_id="s1", prompt="do stuff", workspace="/tmp",
    )
    result = await hook.pre_spawn(ctx)
    # Should not abort (logger not running, drift check fails gracefully)
    assert result.abort is False


@pytest.mark.asyncio
async def test_drift_check_hook_uses_profile_threshold():
    """DriftCheckHook uses threshold from profile when available."""
    hook = DriftCheckHook(threshold=0.7)
    ctx = SpawnContext(
        agent_name="test", agent_id="a1", agent_type="general",
        team_name="team", session_id="s1", prompt="do stuff", workspace="/tmp",
        profile={"drift_threshold": 0.5},
    )
    # Profile threshold is 0.5 — but since logger isn't running, drift returns 0.0
    result = await hook.pre_spawn(ctx)
    assert result.abort is False  # drift 0.0 < 0.5


# ── Session log hook tests ──

@pytest.mark.asyncio
async def test_session_log_hook_creates_session_file(tmp_path):
    """SessionLogHook creates session file on pre_spawn."""
    hook = SessionLogHook(data_dir=str(tmp_path))
    ctx = SpawnContext(
        agent_name="reviewer", agent_id="a1", agent_type="browser-review",
        team_name="my-team", session_id="s1", prompt="Review PR #42", workspace="/tmp",
    )

    result = await hook.pre_spawn(ctx)
    assert result.abort is False

    session_file = tmp_path / "sessions" / "my-team" / "reviewer.json"
    assert session_file.exists()

    data = json.loads(session_file.read_text())
    assert data["agentName"] == "reviewer"
    assert data["state"]["status"] == "spawning"


@pytest.mark.asyncio
async def test_session_log_hook_updates_on_completion(tmp_path):
    """SessionLogHook updates session file on post_spawn."""
    hook = SessionLogHook(data_dir=str(tmp_path))
    ctx = SpawnContext(
        agent_name="reviewer", agent_id="a1", agent_type="browser-review",
        team_name="my-team", session_id="s1", prompt="Review PR #42", workspace="/tmp",
    )

    # Pre-spawn to create file
    await hook.pre_spawn(ctx)

    # Simulate result
    mock_result = HarnessResult(
        exit_code=0, output="done", tool_calls=5, duration_ms=1200,
        session_id="s1",
    )

    await hook.post_spawn(ctx, mock_result)

    session_file = tmp_path / "sessions" / "my-team" / "reviewer.json"
    data = json.loads(session_file.read_text())
    assert data["state"]["status"] == "completed"
    assert data["state"]["tool_calls"] == 5
    assert data["state"]["duration_ms"] == 1200
