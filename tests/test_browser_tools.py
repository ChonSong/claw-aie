"""Tests for browser tools and extended ToolExecutor."""

import asyncio
import json
import pytest

from aie_integration.tool_executor import ToolExecutor
from aie_integration.browser_tools import BROWSER_TOOLS, cleanup_browser


@pytest.fixture
def executor_with_browser():
    """ToolExecutor with browser tools registered."""
    executor = ToolExecutor()
    executor.register_browser_tools()
    yield executor
    # Cleanup browser after test
    asyncio.get_event_loop().run_until_complete(cleanup_browser())


def test_browser_tools_registered():
    """All browser tools are in the registry."""
    expected = {
        "browser_navigate",
        "browser_screenshot",
        "browser_click",
        "browser_fill",
        "browser_console",
        "browser_assert",
        "browser_assert_no_console_errors",
        "browser_accessibility_scan",
    }
    assert set(BROWSER_TOOLS.keys()) == expected


def test_executor_registers_browser_tools():
    """ToolExecutor.register_browser_tools() adds all browser tools to custom tools."""
    executor = ToolExecutor()
    executor.register_browser_tools()
    for name in BROWSER_TOOLS:
        assert name in executor._custom_tools


def test_custom_tool_dispatch():
    """Custom registered tools are dispatched correctly."""
    executor = ToolExecutor()

    async def my_tool(inp: dict):
        from aie_integration.tool_executor import ToolResult
        return ToolResult(tool_name="my_tool", output=f"got {inp.get('x')}", exit_code=0, duration_ms=0)

    executor.register_tool("my_tool", my_tool)

    result = asyncio.get_event_loop().run_until_complete(
        executor.execute("my_tool", {"x": 42})
    )
    assert result.tool_name == "my_tool"
    assert result.output == "got 42"
    assert result.exit_code == 0


def test_unknown_tool_returns_error():
    """Unknown tool returns error ToolResult."""
    executor = ToolExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        executor.execute("nonexistent_tool", {})
    )
    assert result.exit_code == 1
    assert "Unknown tool" in result.error


def test_browser_navigate_missing_url():
    """browser_navigate with no URL returns error."""
    result = asyncio.get_event_loop().run_until_complete(
        BROWSER_TOOLS["browser_navigate"]({})
    )
    assert result.exit_code == 1
    assert "url is required" in result.output


def test_browser_assert_missing_selector():
    """browser_assert with no selector returns error."""
    result = asyncio.get_event_loop().run_until_complete(
        BROWSER_TOOLS["browser_assert"]({})
    )
    # Should fail because no element found (no browser open)
    assert result.exit_code == 1


def test_browser_click_missing_selector():
    """browser_click with no selector returns error."""
    result = asyncio.get_event_loop().run_until_complete(
        BROWSER_TOOLS["browser_click"]({})
    )
    assert result.exit_code == 1
    assert "selector is required" in result.output


def test_browser_fill_missing_selector():
    """browser_fill with no selector returns error."""
    result = asyncio.get_event_loop().run_until_complete(
        BROWSER_TOOLS["browser_fill"]({})
    )
    assert result.exit_code == 1
    assert "selector is required" in result.output
