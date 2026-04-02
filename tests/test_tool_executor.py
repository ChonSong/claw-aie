import pytest
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from aie_integration.tool_executor import ToolExecutor

@pytest.mark.asyncio
async def test_bash_execution():
    executor = ToolExecutor(workspace_root="/tmp")
    result = await executor.execute("bash", {"command": "echo hello"})
    assert result.exit_code == 0
    assert "hello" in result.output

@pytest.mark.asyncio
async def test_file_read():
    executor = ToolExecutor(workspace_root="/tmp")
    Path("/tmp/test_claw_aie.txt").write_text("hello world")
    result = await executor.execute("file_read", {"path": "test_claw_aie.txt"})
    assert result.exit_code == 0
    assert "hello world" in result.output

@pytest.mark.asyncio
async def test_file_write():
    executor = ToolExecutor(workspace_root="/tmp")
    result = await executor.execute("file_write", {"path": "test_claw_aie_write.txt", "content": "test content"})
    assert result.exit_code == 0
    assert Path("/tmp/test_claw_aie_write.txt").read_text() == "test content"

@pytest.mark.asyncio
async def test_glob():
    executor = ToolExecutor(workspace_root="/tmp")
    Path("/tmp/test_glob_abc.txt").touch()
    result = await executor.execute("glob", {"pattern": "test_glob*.txt"})
    assert result.exit_code == 0

@pytest.mark.asyncio
async def test_sanitiser():
    from aie_integration.sanitiser import sanitise
    data = {"password": "secret123", "command": "ls"}
    result = sanitise(data)
    assert result["password"] == "[REDACTED]"
    assert result["command"] == "ls"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
