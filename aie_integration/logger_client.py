"""Shared AIE logger client — single implementation for all socket IPC.

Used by AIEEventEmitter, Harness, SpawnHooks, and CLI.
Replaces 5 copy-pasted socket connection patterns.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any


class AIELoggerClient:
    """Async JSON-RPC client for the AIE logger Unix socket."""

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")

    async def emit(self, event: dict[str, Any]) -> dict | None:
        """Emit an event to the AIE logger. Returns response or None."""
        return await self._send("emit", {"event": event})

    async def query(self, method: str, params: dict | None = None) -> dict | None:
        """Send arbitrary JSON-RPC query."""
        return await self._send(method, params or {})

    async def _send(self, method: str, params: dict) -> dict | None:
        """Send JSON-RPC 2.0 request, return parsed response."""
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            request = json.dumps({
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": 0,
            }).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            response_bytes = await asyncio.wait_for(reader.readline(), timeout=5)
            writer.close()
            await writer.wait_closed()
            if response_bytes:
                return json.loads(response_bytes.decode("utf-8"))
            return None
        except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError, OSError):
            return None
        except Exception:
            return None

    @property
    def is_available(self) -> bool:
        """Check if the logger socket exists."""
        return os.path.exists(self.socket_path)
