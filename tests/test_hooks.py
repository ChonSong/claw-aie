"""TDD tests for the hook system — Phase B."""
import pytest
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from aie_integration.tool_executor import ToolExecutor
from aie_integration.hooks.base import HookResult, ToolHook
from aie_integration.hooks.runner import HookRunner
from aie_integration.hooks.permission_hook import PermissionHook
from aie_integration.hooks.rate_limit_hook import RateLimitHook
from aie_integration.config import load_hooks_config, build_hook_runner


# =============================================================================
# PermissionHook Tests
# =============================================================================

class TestPermissionHook:
    """Test PermissionHook blocks dangerous commands."""

    @pytest.fixture
    def hook(self):
        return PermissionHook()

    @pytest.mark.asyncio
    async def test_denies_rm_rf(self, hook):
        """Permission hook must deny 'rm -rf' commands."""
        result = await hook.pre_tool_use("bash", {"command": "rm -rf /tmp/some_dir"})
        assert result is not None
        assert result.denied is True
        assert "rm -rf" in result.denial_message.lower() or "destructive" in result.denial_message.lower()

    @pytest.mark.asyncio
    async def test_denies_dd_if(self, hook):
        """Permission hook must deny 'dd if=' commands."""
        result = await hook.pre_tool_use("bash", {"command": "dd if=/dev/zero of=/dev/sda"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_denies_dev_sda(self, hook):
        """Permission hook must deny commands writing to /dev/sda."""
        result = await hook.pre_tool_use("bash", {"command": "echo hello > /dev/sda"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_denies_file_write_to_etc(self, hook):
        """Permission hook must deny file_write to /etc."""
        result = await hook.pre_tool_use("file_write", {"path": "/etc/passwd", "content": "malicious"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_denies_file_write_to_usr(self, hook):
        """Permission hook must deny file_write to /usr."""
        result = await hook.pre_tool_use("file_write", {"path": "/usr/bin/malware", "content": "bad"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_denies_file_write_to_bin(self, hook):
        """Permission hook must deny file_write to /bin."""
        result = await hook.pre_tool_use("file_write", {"path": "/bin/evil", "content": "bad"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_denies_file_write_to_sbin(self, hook):
        """Permission hook must deny file_write to /sbin."""
        result = await hook.pre_tool_use("file_write", {"path": "/sbin/malicious", "content": "bad"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_denies_file_write_to_boot(self, hook):
        """Permission hook must deny file_write to /boot."""
        result = await hook.pre_tool_use("file_write", {"path": "/boot/grub/malicious", "content": "bad"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_denies_file_write_to_sys(self, hook):
        """Permission hook must deny file_write to /sys."""
        result = await hook.pre_tool_use("file_write", {"path": "/sys/kernel/malicious", "content": "bad"})
        assert result is not None
        assert result.denied is True

    @pytest.mark.asyncio
    async def test_allows_safe_bash(self, hook):
        """Permission hook must allow safe bash commands."""
        result = await hook.pre_tool_use("bash", {"command": "echo hello"})
        assert result is None or result.denied is False

    @pytest.mark.asyncio
    async def test_allows_safe_file_read(self, hook):
        """Permission hook must allow safe file_read."""
        result = await hook.pre_tool_use("file_read", {"path": "/tmp/safe.txt"})
        assert result is None or result.denied is False

    @pytest.mark.asyncio
    async def test_allows_safe_file_write(self, hook):
        """Permission hook must allow safe file_write to user directories."""
        result = await hook.pre_tool_use("file_write", {"path": "/tmp/safe.txt", "content": "safe"})
        assert result is None or result.denied is False


# =============================================================================
# RateLimitHook Tests
# =============================================================================

class TestRateLimitHook:
    """Test RateLimitHook enforces per-tool rate limiting."""

    @pytest.mark.asyncio
    async def test_allows_first_requests(self):
        """First N requests should be allowed."""
        hook = RateLimitHook(capacity=3, refill_rate=10.0)
        for _ in range(3):
            result = await hook.pre_tool_use("bash", {"command": "echo hi"})
            assert result is None or result.denied is False

    @pytest.mark.asyncio
    async def test_denies_when_exhausted(self):
        """When tokens are exhausted, deny requests."""
        hook = RateLimitHook(capacity=2, refill_rate=0.1)
        # Exhaust tokens
        await hook.pre_tool_use("bash", {"command": "echo 1"})
        await hook.pre_tool_use("bash", {"command": "echo 2"})
        # Now should be denied
        result = await hook.pre_tool_use("bash", {"command": "echo 3"})
        assert result is not None
        assert result.denied is True
        assert "Rate limit" in result.denial_message

    @pytest.mark.asyncio
    async def test_refills_over_time(self):
        """Tokens should refill over time."""
        hook = RateLimitHook(capacity=1, refill_rate=5.0)  # 5 tokens per second
        # Exhaust the single token
        await hook.pre_tool_use("bash", {"command": "echo 1"})
        result = await hook.pre_tool_use("bash", {"command": "echo 2"})
        assert result is not None
        assert result.denied is True
        # Wait for refill
        await asyncio.sleep(0.3)  # ~1.5 tokens should be available
        result = await hook.pre_tool_use("bash", {"command": "echo 3"})
        assert result is None or result.denied is False

    @pytest.mark.asyncio
    async def test_different_tools_have_independent_limits(self):
        """Rate limits should be per-tool, not global."""
        hook = RateLimitHook(capacity=1, refill_rate=0.1)
        # Exhaust bash
        await hook.pre_tool_use("bash", {"command": "echo 1"})
        # glob should still have capacity
        result = await hook.pre_tool_use("glob", {"pattern": "*.txt"})
        assert result is None or result.denied is False


# =============================================================================
# HookRunner Tests
# =============================================================================

class TestHookRunner:
    """Test HookRunner executes hooks in sequence."""

    @pytest.fixture
    def mock_executor(self):
        executor = MagicMock()
        return executor

    @pytest.mark.asyncio
    async def test_runs_pre_hooks_sequentially(self, mock_executor):
        """Pre hooks should run in registration order."""
        runner = HookRunner(mock_executor)
        call_order = []

        class TrackingHook(ToolHook):
            async def pre_tool_use(self, tool_name, tool_input):
                call_order.append(tool_name)
                return None
            async def post_tool_use(self, tool_name, tool_input, output):
                pass

        hook1 = TrackingHook()
        hook2 = TrackingHook()
        runner.register_pre(hook1)
        runner.register_pre(hook2)
        await runner.run_pre_tool_use("test_tool", {})
        assert call_order == ["test_tool", "test_tool"]

    @pytest.mark.asyncio
    async def test_first_denial_stops_execution(self, mock_executor):
        """First pre hook denial should stop execution and return immediately."""
        runner = HookRunner(mock_executor)

        class DenyingHook(ToolHook):
            async def pre_tool_use(self, tool_name, tool_input):
                return HookResult(denied=True, denial_message="Denied by hook1")
            async def post_tool_use(self, tool_name, tool_input, output):
                pass

        class NeverRunHook(ToolHook):
            async def pre_tool_use(self, tool_name, tool_input):
                raise AssertionError("This hook should never run")
            async def post_tool_use(self, tool_name, tool_input, output):
                pass

        runner.register_pre(DenyingHook())
        runner.register_pre(NeverRunHook())
        result = await runner.run_pre_tool_use("test_tool", {})
        assert result.denied is True
        assert result.denial_message == "Denied by hook1"

    @pytest.mark.asyncio
    async def test_post_hooks_run_even_on_tool_failure(self, mock_executor):
        """Post hooks should run even when tool execution fails."""
        runner = HookRunner(mock_executor)
        post_called = False

        class TrackingPostHook(ToolHook):
            async def pre_tool_use(self, tool_name, tool_input):
                return None
            async def post_tool_use(self, tool_name, tool_input, output):
                nonlocal post_called
                post_called = True

        runner.register_post(TrackingPostHook())
        await runner.run_post_tool_use("bash", {"command": "exit 1"}, "error output")
        assert post_called is True


# =============================================================================
# ToolExecutor Hook Integration Tests
# =============================================================================

class TestToolExecutorHookIntegration:
    """Test that ToolExecutor properly integrates with HookRunner."""

    @pytest.mark.asyncio
    async def test_executor_returns_denied_result_when_pre_hook_denies(self):
        """When pre hook denies, executor should return denied ToolResult."""
        executor = ToolExecutor(workspace_root="/tmp")
        hook_runner = HookRunner(executor)

        denying_hook = PermissionHook()
        hook_runner.register_pre(denying_hook)

        result = await executor.execute("bash", {"command": "rm -rf /tmp"})

        assert result.denied is True
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_executor_runs_post_hooks_on_success(self):
        """Post hooks should be called after successful execution."""
        executor = ToolExecutor(workspace_root="/tmp")
        hook_runner = HookRunner(executor)

        post_called = False

        class TrackingHook(ToolHook):
            async def pre_tool_use(self, tool_name, tool_input):
                return None
            async def post_tool_use(self, tool_name, tool_input, output):
                nonlocal post_called
                post_called = True

        hook_runner.register_post(TrackingHook())
        executor.hook_runner = hook_runner

        result = await executor.execute("bash", {"command": "echo hello"})
        assert post_called is True

    @pytest.mark.asyncio
    async def test_executor_runs_post_hooks_on_failure(self):
        """Post hooks should be called even when tool execution fails."""
        executor = ToolExecutor(workspace_root="/tmp")
        hook_runner = HookRunner(executor)

        post_called = False

        class TrackingHook(ToolHook):
            async def pre_tool_use(self, tool_name, tool_input):
                return None
            async def post_tool_use(self, tool_name, tool_input, output):
                nonlocal post_called
                post_called = True

        hook_runner.register_post(TrackingHook())
        executor.hook_runner = hook_runner

        # This will fail because /nonexistent_dir is not readable
        result = await executor.execute("bash", {"command": "cat /nonexistent"})
        assert post_called is True

    @pytest.mark.asyncio
    async def test_executor_without_hook_runner_works_normally(self):
        """Executor without hook_runner should execute normally."""
        executor = ToolExecutor(workspace_root="/tmp")
        executor.hook_runner = None

        result = await executor.execute("bash", {"command": "echo hello"})
        assert result.exit_code == 0
        assert "hello" in result.output


# =============================================================================
# Config Tests
# =============================================================================

class TestConfig:
    """Test configuration loading and hook runner building."""

    def test_load_hooks_config_from_file(self, tmp_path):
        """Should load hooks configuration from YAML file."""
        # Create a temporary hooks.yaml
        hooks_yaml = tmp_path / "hooks.yaml"
        hooks_yaml.write_text("""
hooks:
  permission:
    enabled: true
    tools: ["bash", "file_write"]
  rate_limit:
    enabled: true
    tools: ["bash"]
    capacity: 5
    refill_rate: 1.0
""")
        config = load_hooks_config(str(hooks_yaml))
        assert "hooks" in config
        assert "permission" in config["hooks"]
        assert config["hooks"]["permission"]["enabled"] is True
        assert "rate_limit" in config["hooks"]
        assert config["hooks"]["rate_limit"]["capacity"] == 5

    def test_build_hook_runner_creates_permission_hook(self, tmp_path):
        """build_hook_runner should create PermissionHook when configured."""
        hooks_yaml = tmp_path / "hooks.yaml"
        hooks_yaml.write_text("""
hooks:
  permission:
    enabled: true
    tools: ["bash", "file_write"]
""")
        config = load_hooks_config(str(hooks_yaml))
        mock_executor = MagicMock()
        runner = build_hook_runner(config, mock_executor)
        assert len(runner.pre_hooks) >= 1  # At least the permission hook

    def test_build_hook_runner_creates_rate_limit_hook(self, tmp_path):
        """build_hook_runner should create RateLimitHook when configured."""
        hooks_yaml = tmp_path / "hooks.yaml"
        hooks_yaml.write_text("""
hooks:
  rate_limit:
    enabled: true
    tools: ["bash"]
    capacity: 5
    refill_rate: 1.0
""")
        config = load_hooks_config(str(hooks_yaml))
        mock_executor = MagicMock()
        runner = build_hook_runner(config, mock_executor)
        assert len(runner.pre_hooks) >= 1


# =============================================================================
# AIEEmitter Bug Fix Tests
# =============================================================================

class TestAIEEmitterBugFix:
    """Test that aie_emitter.py uses correct default socket path."""

    def test_aie_emitter_uses_tmp_socket_path(self):
        """AIEEmitter should default to /tmp/ailogger.sock, not hardcoded path."""
        from aie_integration.hooks.aie_emitter import AIEEventEmitter
        import os

        # The socket path should be /tmp/ailogger.sock by default
        emitter = AIEEventEmitter()
        assert emitter.socket_path == "/tmp/ailogger.sock"

    def test_aie_emitter_respects_env_var(self):
        """AIEEmitter should respect AILOGGER_SOCKET env var."""
        from aie_integration.hooks.aie_emitter import AIEEventEmitter
        import os

        os.environ["AILOGGER_SOCKET"] = "/custom/path.sock"
        emitter = AIEEventEmitter()
        assert emitter.socket_path == "/custom/path.sock"
        del os.environ["AILOGGER_SOCKET"]

    def test_aie_emitter_respects_explicit_path(self):
        """AIEEmitter should respect explicitly passed socket_path."""
        from aie_integration.hooks.aie_emitter import AIEEventEmitter

        emitter = AIEEventEmitter(socket_path="/explicit/path.sock")
        assert emitter.socket_path == "/explicit/path.sock"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])