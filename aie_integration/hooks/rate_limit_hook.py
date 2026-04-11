"""Rate limit hook - per-tool rate limiting using token bucket algorithm."""
import asyncio
import time
from typing import Any

from .base import HookResult, ToolHook


class TokenBucket:
    """Token bucket rate limiter for a single tool."""

    def __init__(self, capacity: int, refill_rate: float):
        """
        Args:
            capacity: Maximum tokens in the bucket.
            refill_rate: Tokens added per second.
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a token.

        Returns:
            True if token was acquired, False if rate limited.
        """
        async with self._lock:
            await self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

    async def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_refill = now


class RateLimitHook(ToolHook):
    """Per-tool rate limiting using token bucket algorithm.

    Each tool has its own token bucket with configurable capacity
    and refill rate.

    Args:
        tools: List of tool names to apply rate limiting to.
               None = all tools.
        capacity: Maximum tokens per tool (default 10).
        refill_rate: Tokens added per second (default 1.0).
    """

    def __init__(
        self,
        tools: list[str] | None = None,
        capacity: int = 10,
        refill_rate: float = 1.0
    ):
        self.tools = tools
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets: dict[str, TokenBucket] = {}

    def _get_bucket(self, tool_name: str) -> TokenBucket:
        """Get or create a token bucket for the given tool."""
        if tool_name not in self._buckets:
            self._buckets[tool_name] = TokenBucket(self.capacity, self.refill_rate)
        return self._buckets[tool_name]

    def _is_applicable(self, tool_name: str) -> bool:
        """Check if this hook applies to the given tool."""
        if self.tools is None:
            return True
        return tool_name in self.tools

    async def pre_tool_use(self, tool_name: str, tool_input: dict) -> HookResult | None:
        """Check if the tool use is within rate limits."""
        if not self._is_applicable(tool_name):
            return None

        bucket = self._get_bucket(tool_name)
        if await bucket.acquire():
            return None

        return HookResult(
            denied=True,
            denial_message=f"Rate limit exceeded for tool '{tool_name}'. Try again later."
        )

    async def post_tool_use(self, tool_name: str, tool_input: dict, output: str) -> None:
        """Post-execution hook - nothing to do for rate limiting."""
        pass