#!/usr/bin/env python3
"""claw-aie CLI — AIE-compatible agent harness entry point.

Wires PortRuntime → ToolExecutor → HookRunner → AIEEventEmitter
into a runnable agent loop with full PreToolUse/PostToolUse hooks.

Usage:
    claw-aie run                 Run interactive agent loop
    claw-aie execute <tool> <input_json>   Execute single tool via hook pipeline
    claw-aie hooks list          List registered hooks
    claw-aie hooks test <tool>  Test hooks for a specific tool
    claw-aie status              Show AIE logger connection status
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure aie_integration is on path
_AIE_INTEGRATION = Path(__file__).parent
sys.path.insert(0, str(_AIE_INTEGRATION.parent))

from aie_integration.session import set_session
from aie_integration.tool_executor import ToolExecutor, ToolResult
from aie_integration.hooks.runner import HookRunner
from aie_integration.hooks.aie_emitter import AIEEventEmitter
from aie_integration.hooks.permission_hook import PermissionHook
from aie_integration.hooks.rate_limit_hook import RateLimitHook
from aie_integration.config import load_hooks_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="claw-aie — AIE-compatible agent harness with PreToolUse/PostToolUse hooks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # claw-aie run
    run_parser = subparsers.add_parser("run", help="Run interactive agent loop")
    run_parser.add_argument(
        "--session-id",
        default="claw-aie-interactive",
        help="Session ID for AIE event correlation",
    )
    run_parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Workspace root directory",
    )
    run_parser.add_argument(
        "--no-aie",
        action="store_true",
        help="Disable AIE event emission",
    )

    # claw-aie execute
    exec_parser = subparsers.add_parser(
        "execute", help="Execute a single tool via the hook pipeline"
    )
    exec_parser.add_argument("tool", help="Tool name (e.g. bash, file_read)")
    exec_parser.add_argument(
        "input_json",
        help="Tool input as JSON string",
    )
    exec_parser.add_argument(
        "--session-id",
        default="claw-aie-one-shot",
    )
    exec_parser.add_argument(
        "--workspace",
        default=os.getcwd(),
    )

    # claw-aie hooks
    hooks_parser = subparsers.add_parser("hooks", help="Hook management")
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command", required=True)
    list_parser = hooks_sub.add_parser("list", help="List all registered hooks")
    test_parser = hooks_sub.add_parser(
        "test", help="Test hooks for a specific tool (dry run)"
    )
    test_parser.add_argument("tool", help="Tool name to test hooks for")

    # claw-aie status
    status_parser = subparsers.add_parser("status", help="Show AIE logger connection status")

    return parser


# ---------------------------------------------------------------------------
# Hook setup
# ---------------------------------------------------------------------------


def build_hook_runner(
    workspace_root: str | None = None,
    aie_enabled: bool = True,
) -> HookRunner:
    """Build and configure the hook runner with all registered hooks."""
    executor = ToolExecutor(workspace_root=workspace_root)
    runner = HookRunner(executor)

    config = load_hooks_config()

    # Always add AIE emitter first (it observes, doesn't block)
    if aie_enabled:
        aie_emitter = AIEEventEmitter()
        runner.register_pre(aie_emitter)
        runner.register_post(aie_emitter)

    # Add permission hook
    permission_hook = PermissionHook()
    runner.register_pre(permission_hook)

    # Add rate limit hook
    rate_limit_hook = RateLimitHook()
    runner.register_pre(rate_limit_hook)
    runner.register_post(rate_limit_hook)

    executor.hook_runner = runner
    return runner


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_run(
    session_id: str,
    workspace: str,
    aie_enabled: bool,
) -> int:
    """Run an interactive agent loop."""
    set_session(session_id, "claw-aie")
    runner = build_hook_runner(workspace_root=workspace, aie_enabled=aie_enabled)
    executor = runner.executor

    print(f"[claw-aie] Session: {session_id}")
    print(f"[claw-aie] Workspace: {workspace}")
    print(f"[claw-aie] AIE emission: {'enabled' if aie_enabled else 'disabled'}")
    print(f"[claw-aie] Pre-hooks: {len(runner.pre_hooks)}, Post-hooks: {len(runner.post_hooks)}")
    print()
    print("claw-aie interactive loop — type a tool call as JSON")
    print("Example: {\"tool\": \"bash\", \"input\": {\"command\": \"ls -la\"}}")
    print("Type 'quit' or 'exit' to stop, 'help' for commands")
    print()

    while True:
        try:
            line = await asyncio.get_event_loop().run_in_executor(
                None, sys.stdin.readline
            )
            if not line:
                break
            line = line.strip()
            if line in ("quit", "exit", "q"):
                print("[claw-aie] Goodbye")
                break
            if line == "help":
                print("Commands: quit, exit, help, status")
                continue
            if line == "status":
                print(f"[claw-aie] Session: {session_id}")
                print(f"[claw-aie] Pre-hooks: {len(runner.pre_hooks)}")
                print(f"[claw-aie] Post-hooks: {len(runner.post_hooks)}")
                continue

            # Parse JSON input
            try:
                call = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[claw-aie] JSON parse error: {e}")
                continue

            tool_name = call.get("tool")
            tool_input = call.get("input", {})
            if not tool_name:
                print("[claw-aie] Missing 'tool' field in JSON")
                continue

            # Execute via hook pipeline
            result: ToolResult = await executor.execute(tool_name, tool_input)

            # Output result as JSON
            output = {
                "tool": result.tool_name,
                "output": result.output,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "denied": result.denied,
                "error": result.error,
            }
            print(json.dumps(output, indent=2))
            print()

        except EOFError:
            break
        except KeyboardInterrupt:
            print("\n[claw-aie] Interrupted")
            break
        except Exception as e:
            print(f"[claw-aie] Error: {e}")

    return 0


async def cmd_execute(
    tool: str,
    input_json: str,
    session_id: str,
    workspace: str,
) -> int:
    """Execute a single tool via the hook pipeline."""
    set_session(session_id, "claw-aie")
    runner = build_hook_runner(workspace_root=workspace, aie_enabled=True)
    executor = runner.executor

    try:
        tool_input = json.loads(input_json)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}", file=sys.stderr)
        return 1

    result: ToolResult = await executor.execute(tool, tool_input)

    output = {
        "tool": result.tool_name,
        "output": result.output,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "denied": result.denied,
        "error": result.error,
    }

    print(json.dumps(output, indent=2))
    return 0


def cmd_hooks_list() -> int:
    """List all registered hooks."""
    runner = build_hook_runner(aie_enabled=False)
    print(f"Pre-tool hooks ({len(runner.pre_hooks)}):")
    for hook in runner.pre_hooks:
        print(f"  - {hook.__class__.__name__}")
    print(f"Post-tool hooks ({len(runner.post_hooks)}):")
    for hook in runner.post_hooks:
        print(f"  - {hook.__class__.__name__}")
    return 0


async def cmd_hooks_test(tool: str) -> int:
    """Test hooks for a specific tool (dry run)."""
    runner = build_hook_runner(aie_enabled=True)
    executor = runner.executor

    # Generate a dummy input based on tool name
    dummy_inputs = {
        "bash": {"command": "echo 'test'"},
        "file_read": {"path": "README.md"},
        "file_write": {"path": "test.txt", "content": "test"},
        "glob": {"pattern": "*.py"},
        "grep": {"pattern": "TODO", "path": "."},
    }
    tool_input = dummy_inputs.get(tool, {})

    print(f"[claw-aie] Testing pre_tool_use hooks for tool: {tool}")
    pre_result = await runner.run_pre_tool_use(tool, tool_input)
    if pre_result.denied:
        print(f"[claw-aie] BLOCKED: {pre_result.denial_message}")
    else:
        print(f"[claw-aie] ALLOWED (pre hooks passed)")
        print(f"[claw-aie] Would execute: {tool} with input: {json.dumps(tool_input)}")

    return 0


def cmd_status() -> int:
    """Show AIE logger connection status."""
    import socket

    sock_path = os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")
    print(f"[claw-aie] AIE socket: {sock_path}")

    if not os.path.exists(sock_path):
        print("[claw-aie] Socket file does not exist — AIE logger not running?")
        return 1

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(sock_path)
        req = (
            json.dumps(
                {"jsonrpc": "2.0", "method": "status", "params": {}, "id": 1}
            ).encode()
            + b"\n"
        )
        sock.sendall(req)
        resp = sock.recv(4096)
        sock.close()
        data = json.loads(resp.decode())
        result = data.get("result", {})
        print(f"[claw-aie] Logger uptime: {result.get('logger_uptime_seconds', 0):.0f}s")
        print(f"[claw-aie] Events received: {result.get('events_received', 0)}")
        print(f"[claw-aie] Buffered: {result.get('buffered', 0)}")
        print(f"[claw-aie] txtai available: {result.get('txtai_available', False)}")
        return 0
    except Exception as e:
        print(f"[claw-aie] Socket error: {e}")
        return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return asyncio.run(
            cmd_run(
                session_id=args.session_id,
                workspace=args.workspace,
                aie_enabled=not args.no_aie,
            )
        )

    if args.command == "execute":
        return asyncio.run(
            cmd_execute(
                tool=args.tool,
                input_json=args.input_json,
                session_id=args.session_id,
                workspace=args.workspace,
            )
        )

    if args.command == "hooks":
        if args.hooks_command == "list":
            return cmd_hooks_list()
        if args.hooks_command == "test":
            return asyncio.run(cmd_hooks_test(args.tool))

    if args.command == "status":
        return cmd_status()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())