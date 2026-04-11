"""Configuration loading for claw-aie hooks."""
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # yaml is optional


def load_hooks_config(config_path: str | None = None) -> dict[str, Any]:
    """Load hooks configuration from YAML file.

    Args:
        config_path: Path to hooks.yaml. If None, searches in:
            - ~/.claw-aie/hooks.yaml
            - ./hooks.yaml
            - ./aie_integration/hooks.yaml

    Returns:
        Dict with hooks configuration.
    """
    if config_path is None:
        # Search for config in standard locations
        search_paths = [
            Path.home() / ".claw-aie" / "hooks.yaml",
            Path("hooks.yaml"),
            Path(__file__).parent / "hooks.yaml",
        ]
        for path in search_paths:
            if path.exists():
                config_path = str(path)
                break

    if config_path is None:
        return {"hooks": {}}

    config_path = Path(config_path)
    if not config_path.exists():
        return {"hooks": {}}

    if yaml is None:
        # Fallback: simple line-based parsing for basic cases
        return _parse_simple_yaml(config_path)

    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {"hooks": {}}


def _parse_simple_yaml(path: Path) -> dict[str, Any]:
    """Fallback YAML parser for environments without pyyaml."""
    result = {"hooks": {}}
    current_section = None
    current_hook = None

    with open(path, "r") as f:
        for line in f:
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue

            # Top-level key
            if line.startswith("hooks:"):
                continue

            # Hook name (e.g., "  permission:")
            if line.startswith("  ") and not line.startswith("    "):
                hook_name = line.strip().rstrip(":")
                current_hook = hook_name
                result["hooks"][current_hook] = {}
                current_section = result["hooks"][current_hook]

            # Key-value pair
            elif line.startswith("    ") and current_hook:
                parts = line.strip().split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()

                    # Parse value types
                    if value == "true":
                        value = True
                    elif value == "false":
                        value = False
                    elif value.isdigit():
                        value = int(value)
                    elif value.replace(".", "", 1).isdigit():
                        value = float(value)

                    current_section[key] = value

    return result


def build_hook_runner(config: dict[str, Any], executor: Any) -> "HookRunner":
    """Build a HookRunner from configuration.

    Args:
        config: Hooks configuration dict.
        executor: ToolExecutor instance to pass to HookRunner.

    Returns:
        Configured HookRunner with registered hooks.
    """
    from aie_integration.hooks.runner import HookRunner
    from aie_integration.hooks.permission_hook import PermissionHook
    from aie_integration.hooks.rate_limit_hook import RateLimitHook

    runner = HookRunner(executor)
    hooks_config = config.get("hooks", {})

    # Permission hook
    perm_config = hooks_config.get("permission", {})
    if perm_config.get("enabled", False):
        tools = perm_config.get("tools", None)
        runner.register_pre(PermissionHook(tools=tools))

    # Rate limit hook
    rate_config = hooks_config.get("rate_limit", {})
    if rate_config.get("enabled", False):
        tools = rate_config.get("tools", None)
        capacity = rate_config.get("capacity", 10)
        refill_rate = rate_config.get("refill_rate", 1.0)
        runner.register_pre(RateLimitHook(tools=tools, capacity=capacity, refill_rate=refill_rate))

    # AIE event emitter hook (post-hook — runs after tool execution)
    aie_config = hooks_config.get("aie_emitter", {})
    if aie_config.get("enabled", True):
        from .hooks.aie_emitter import AIEEventEmitter

        emitter = AIEEventEmitter(
            socket_path=aie_config.get("socket_path", "/tmp/ailogger.sock"),
            session_id=aie_config.get("session_id", "default"),
        )
        runner.register_post(emitter)

    return runner