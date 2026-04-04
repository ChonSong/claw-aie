#!/usr/bin/env python3
"""Dry-run test for claw-aie's AIE hook pipeline.

Tests the full hook → socket → AILogger pipeline without needing a real LLM.
Verifies that PreToolUse and PostToolUse events reach the logger.

Usage:
    python3 scripts/test_aie_hook.py
"""
import asyncio
import json
import sys
import os

# Add the aie_integration package to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aie_integration.hooks.runner import HookRunner
from aie_integration.hooks.aie_emitter import AIEEventEmitter

AIE_SOCK = os.environ.get(
    "AILOGGER_SOCKET",
    "/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/evaluator/data/ailogger.sock",
)


class MockToolExecutor:
    """Simulates tool execution (bash, file_read, glob)."""

    async def execute(self, tool_name: str, tool_input: dict) -> dict:
        await asyncio.sleep(0.02)
        if tool_name == "bash":
            return {"output": f"executed bash: {tool_input.get('command', '')}", "exit_code": 0}
        elif tool_name == "file_read":
            path = tool_input.get("path", "")
            if os.path.exists(path):
                with open(path) as f:
                    content = f.read()
                return {"output": content[:200], "exit_code": 0}
            return {"output": f"File not found: {path}", "exit_code": 1}
        elif tool_name == "glob":
            return {"output": "file1.py\nfile2.py\nfile3.py", "exit_code": 0}
        return {"output": f"Unknown tool: {tool_name}", "exit_code": 1}


async def get_logger_status() -> int:
    """Get current events_received count from logger."""
    reader, writer = await asyncio.open_unix_connection(AIE_SOCK)
    req = json.dumps({"jsonrpc": "2.0", "method": "status", "params": {}, "id": 99}).encode() + b"\n"
    writer.write(req)
    await writer.drain()
    resp = await asyncio.wait_for(reader.readline(), timeout=3)
    writer.close()
    await writer.wait_closed()
    result = json.loads(resp.decode())
    return result.get("result", {}).get("events_received", 0)


async def main() -> bool:
    executor = MockToolExecutor()
    runner = HookRunner(executor)
    emitter = AIEEventEmitter(socket_path=AIE_SOCK, session_id="dry-run-test")
    runner.register_pre(emitter)
    runner.register_post(emitter)

    baseline = await get_logger_status()
    print(f"Baseline events in logger: {baseline}")

    # Test 1: bash tool (succeeds)
    print("\n[1] Testing bash tool (success case)...")
    pre1 = await runner.run_pre_tool_use("bash", {"command": "echo hello from claw-aie"})
    result1 = await executor.execute("bash", {"command": "echo hello from claw-aie"})
    await runner.run_post_tool_use("bash", {"command": "echo hello from claw-aie"}, result1["output"])
    print(f"    pre denied: {pre1.denied}, exit: {result1['exit_code']}")

    # Test 2: file_read (file not found — error case)
    print("\n[2] Testing file_read tool (error case)...")
    pre2 = await runner.run_pre_tool_use("file_read", {"path": "/nonexistent/file.txt"})
    result2 = await executor.execute("file_read", {"path": "/nonexistent/file.txt"})
    await runner.run_post_tool_use("file_read", {"path": "/nonexistent/file.txt"}, result2["output"])
    print(f"    pre denied: {pre2.denied}, exit: {result2['exit_code']}")

    # Test 3: glob tool
    print("\n[3] Testing glob tool...")
    pre3 = await runner.run_pre_tool_use("glob", {"pattern": "*.py"})
    result3 = await executor.execute("glob", {"pattern": "*.py"})
    await runner.run_post_tool_use("glob", {"pattern": "*.py"}, result3["output"])
    print(f"    pre denied: {pre3.denied}, exit: {result3['exit_code']}")

    # Verify events landed
    await asyncio.sleep(0.3)  # give async sends time to complete
    final = await get_logger_status()
    new_events = final - baseline
    print(f"\n[+] New events in logger: {new_events} (expected 6 — 3 pre + 3 post)")

    if new_events >= 6:
        print("\n✅ Pipeline working: claw-aie hooks → AILogger socket confirmed!")
        return True
    else:
        print(f"\n❌ Undercount: expected 6 new events, got {new_events}")
        return False


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
