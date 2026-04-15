"""ClawTeam spawn backend that routes through claw-aie harness.

Implements ClawTeam's SpawnBackend ABC so it can be used as a drop-in
replacement for the default tmux/subprocess backend, adding AIE event
emission for every tool call and session lifecycle event.

Usage in ClawTeam config (~/.clawteam/config.json):
    {
      "default_backend": "aie"
    }

Or per-spawn:
    clawteam spawn --backend aie --agent browser-review --prompt "Review PR #42"
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from clawteam.spawn.base import SpawnBackend
from clawteam.spawn.adapters import NativeCliAdapter
from clawteam.spawn.command_validation import normalize_spawn_command, validate_spawn_command

from .harness import Harness, HarnessResult
from .hooks.aie_emitter import AIEEventEmitter
from .browser_tools import cleanup_browser


class AIESpawnBackend(SpawnBackend):
    """ClawTeam spawn backend powered by claw-aie harness.

    Instead of launching agents as fire-and-forget subprocesses, this backend:
    1. Creates a Harness with AIE hooks registered
    2. Launches the agent CLI through the harness
    3. Parses output for tool calls → routes through hook pipeline → AIE events
    4. Emits session lifecycle events (start, end, timeout)
    """

    def __init__(self):
        self._adapter = NativeCliAdapter()
        self._results: dict[str, HarnessResult] = {}
        self._processes: dict[str, subprocess.Popen] = {}

    def spawn(
        self,
        command: list[str],
        agent_name: str,
        agent_id: str,
        agent_type: str,
        team_name: str,
        prompt: str | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        skip_permissions: bool = False,
    ) -> str:
        """Spawn agent through claw-aie harness. Sync wrapper for async run."""
        # Validate command
        normalized = normalize_spawn_command(command)
        validation_error = validate_spawn_command(normalized)
        if validation_error:
            return validation_error

        # Prepare command
        prepared = self._adapter.prepare_command(
            command,
            prompt=prompt,
            cwd=cwd,
            skip_permissions=skip_permissions,
            agent_name=agent_name,
            interactive=False,
        )

        # Build env
        spawn_env = os.environ.copy()
        spawn_env.update({
            "CLAWTEAM_AGENT_ID": agent_id,
            "CLAWTEAM_AGENT_NAME": agent_name,
            "CLAWTEAM_AGENT_TYPE": agent_type,
            "CLAWTEAM_TEAM_NAME": team_name,
        })
        if env:
            spawn_env.update(env)

        workspace = cwd or os.getcwd()

        # Determine if we should use harness (interactive monitoring) or
        # fire-and-forget subprocess (for long-running agents)
        use_harness = os.environ.get("CLAW_AIE_HARNESS_MODE", "auto")

        if use_harness == "always" or (use_harness == "auto" and agent_type == "browser-review"):
            # Use harness for instrumented execution
            return self._spawn_with_harness(
                command=prepared.final_command,
                agent_name=agent_name,
                agent_id=agent_id,
                agent_type=agent_type,
                team_name=team_name,
                prompt=prompt or "",
                env=spawn_env,
                workspace=workspace,
            )
        else:
            # Fall back to subprocess spawn (same as SubprocessBackend)
            return self._spawn_subprocess(
                command=prepared.final_command,
                agent_name=agent_name,
                agent_id=agent_id,
                team_name=team_name,
                env=spawn_env,
                workspace=workspace,
            )

    def _spawn_with_harness(
        self,
        command: list[str],
        agent_name: str,
        agent_id: str,
        agent_type: str,
        team_name: str,
        prompt: str,
        env: dict[str, str],
        workspace: str,
    ) -> str:
        """Spawn agent through harness (instrumented, blocking)."""
        session_id = f"{team_name}-{agent_name}-{agent_id[:8]}"

        # Build harness
        harness = Harness(
            workspace_root=workspace,
            session_id=session_id,
            agent_id=agent_id,
        )
        harness.register_hook(AIEEventEmitter(session_id=session_id))

        # Register browser tools for browser-review agents
        if agent_type == "browser-review":
            harness.register_browser_tools()

        # Run in background thread (spawn() is sync)
        import threading

        def _run():
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    harness.run_agent(
                        command=command,
                        prompt=prompt,
                        env=env,
                        timeout=int(os.environ.get("CLAW_AIE_TIMEOUT", "600")),
                    )
                )
                self._results[agent_name] = result
            finally:
                loop.close()
                # Cleanup browser if used
                if agent_type == "browser-review":
                    loop2 = asyncio.new_event_loop()
                    loop2.run_until_complete(cleanup_browser())
                    loop2.close()

        thread = threading.Thread(target=_run, daemon=True, name=f"aie-{agent_name}")
        thread.start()

        return f"Agent '{agent_name}' spawned via claw-aie harness (session={session_id})"

    def _spawn_subprocess(
        self,
        command: list[str],
        agent_name: str,
        agent_id: str,
        team_name: str,
        env: dict[str, str],
        workspace: str,
    ) -> str:
        """Spawn agent as subprocess (fire-and-forget, like default backend)."""
        cmd_str = " ".join(shlex.quote(c) for c in command)
        clawteam_bin = "clawteam"
        exit_hook = (
            f"{clawteam_bin} lifecycle on-exit --team {shlex.quote(team_name)} "
            f"--agent {shlex.quote(agent_name)}"
        )
        shell_cmd = f"{cmd_str}; {exit_hook}"

        process = subprocess.Popen(
            shell_cmd,
            shell=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=workspace,
        )
        self._processes[agent_name] = process

        # Register with ClawTeam's spawn registry
        try:
            from clawteam.spawn.registry import register_agent
            register_agent(
                team_name=team_name,
                agent_name=agent_name,
                backend="aie",
                pid=process.pid,
                command=list(command),
            )
        except ImportError:
            pass

        return f"Agent '{agent_name}' spawned as subprocess via AIE backend (pid={process.pid})"

    def list_running(self) -> list[dict[str, str]]:
        """List currently running agents."""
        result = []
        for name, proc in list(self._processes.items()):
            if proc.poll() is None:
                result.append({"name": name, "pid": str(proc.pid), "backend": "aie"})
            else:
                self._processes.pop(name, None)
        return result

    def get_result(self, agent_name: str) -> HarnessResult | None:
        """Get the harness result for a completed agent."""
        return self._results.get(agent_name)

    def register_backend(self) -> None:
        """Register this backend with ClawTeam.

        Call once at startup:
            backend = AIESpawnBackend()
            backend.register_backend()
        """
        try:
            from clawteam.spawn import register_backend as _register
            _register("aie", self)
        except (ImportError, AttributeError):
            # Older ClawTeam versions — backend used via manual config
            pass
