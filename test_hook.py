#!/usr/bin/env python3
"""Dry-run test for claw-aie → AILogger pipeline.

Verifies that PreToolUse + PostToolUse events from the hook runner
actually reach and are accepted by the AILogger socket server.

Run:
    AILOGGER_SOCKET=/path/to/ailogger.sock python3 test_hook.py

Or with defaults:
    cd /path/to/claw-aie && python3 test_hook.py
"""
import asyncio
import json
import os
import sys

AIE_SOCK = os.environ.get(
    "AILOGGER_SOCKET",
    "/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/evaluator/data/ailogger.sock",
)


async def logger_event_count() -> int:
    r, w = await asyncio.open_unix_connection(AIE_SOCK)
    req = json.dumps({"jsonrpc": "2.0", "method": "status", "params": {}, "id": 99}).encode() + b"\n"
    w.write(req)
    await w.drain()
    resp = await asyncio.wait_for(r.readline(), timeout=3)
    w.close()
    await w.wait_closed()
    return json.loads(resp.decode()).get("result", {}).get("events_received", 0)


async def main() -> bool:
    sys.path.insert(0, os.path.dirname(__file__))
    from aie_integration.hooks.runner import HookRunner
    from aie_integration.hooks.aie_emitter import AIEEventEmitter

    class MockToolExecutor:
        async def execute(self, tool_name: str, tool_input: dict) -> dict:
            await asyncio.sleep(0.02)
            if tool_name == "bash":
                return {"output": f"executed: {tool_input.get('command', '')}", "exit_code": 0}
            if tool_name == "file_read":
                return {"output": "File not found", "exit_code": 1}
            return {"output": "ok", "exit_code": 0}

    baseline = await logger_event_count()
    print(f"Baseline events in logger: {baseline}")

    executor = MockToolExecutor()
    runner = HookRunner(executor)
    emitter = AIEEventEmitter(socket_path=AIE_SOCK, session_id="test-hook-001")
    runner.register_pre(emitter)
    runner.register_post(emitter)

    test_cases = [
        ("bash",        {"command": "echo hello from claw-aie"}),
        ("file_read",   {"path": "/nonexistent/file.txt"}),
        ("bash",        {"command": "ls -la"}),
    ]

    for tool_name, tool_input in test_cases:
        pre_result = await runner.run_pre_tool_use(tool_name, tool_input)
        exec_result = await executor.execute(tool_name, tool_input)
        await runner.run_post_tool_use(tool_name, tool_input, exec_result["output"])
        print(f"  [{tool_name}] pre_denied={pre_result.denied} exit={exec_result['exit_code']}")

    await asyncio.sleep(0.5)
    final = await logger_event_count()
    new_events = final - baseline
    expected = len(test_cases) * 2  # 1 pre + 1 post per tool

    print(f"\nNew events: {new_events} (expected {expected})")
    if new_events == expected:
        print("✅ PASS — claw-aie hook → AILogger pipeline confirmed working")
        return True
    else:
        print(f"❌ FAIL — expected {expected} events, got {new_events}")
        return False


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
