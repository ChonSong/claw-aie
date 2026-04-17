# ClawTeam × claw-aie Integration Spec

> Wire claw-aie as ClawTeam's execution backend so every agent turn emits structured AIE events.

---

## 1. Problem

ClawTeam launches agent CLIs (claude, codex, gemini, etc.) as opaque processes. It knows *when* an agent started/stopped but not *what* it did. The sidecar bridges delegation events, but tool calls, assumptions, and corrections inside agent turns are invisible to AIE.

## 2. Solution

Replace raw CLI spawn with **claw-aie as the execution harness**. Each "agent" in ClawTeam becomes:

```
ClawTeam profile → claw-aie harness → agent CLI (wrapped) → hooks → AIE events
```

The agent CLI still runs — but claw-aie wraps it, intercepting tool calls and emitting events.

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ ClawTeam (orchestration layer — unchanged)                   │
│                                                               │
│  TeamManager ──► TaskStore ──► Mailbox                       │
│       │                                                       │
│       ▼ spawn_agent()                                        │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ SpawnBackend (new: AIEBackend)                          │ │
│  │                                                         │ │
│  │  Before: tmux → claude --print "task prompt"            │ │
│  │  After:  claw-aie run --profile <name> --prompt "..."   │ │
│  │                                                         │ │
│  │  ┌────────────────────────────────────────────────────┐ │ │
│  │  │ claw-aie harness                                   │ │ │
│  │  │                                                    │ │ │
│  │  │  1. Load profile (agent CLI + model + env)         │ │ │
│  │  │  2. Register tools + hooks                         │ │ │
│  │  │  3. Launch agent CLI as subprocess                 │ │ │
│  │  │  4. Intercept stdout/stderr → parse tool calls    │ │ │
│  │  │  5. Emit AIE events per tool call                 │ │ │
│  │  │  6. On completion → emit task completion event   │ │ │
│  │  └────────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          │                                    │
│                          ▼                                    │
│                    ailogger.sock                              │
│                          │                                    │
│  ┌───────────────────────▼─────────────────────────────────┐ │
│  │ AIE pipeline (unchanged)                                   │ │
│  │ drift_scan → drift_check → oracle_batch → alert            │ │
│  └───────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

## 4. Changes Required

### 4.1 New: `AIEBackend` (claw-aie)

File: `claw-aie/aie_integration/spawn_backend.py`

Replaces ClawTeam's tmux/subprocess spawn with claw-aie harness execution.

```python
class AIESpawnBackend:
    """ClawTeam spawn backend that routes through claw-aie harness."""

    async def spawn(
        self,
        profile: AgentProfile,
        task_prompt: str,
        workspace: str,
        session_id: str,
    ) -> SpawnResult:
        # 1. Build harness with profile's tools + hooks
        harness = Harness(
            workspace_root=workspace,
            session_id=session_id,
            agent_id=profile.name,
        )
        harness.register_hooks(AIEEventEmitter())

        # 2. Load profile-specific tools
        for tool_spec in profile.tools:
            harness.register_tool(tool_spec)

        # 3. Execute agent CLI, intercepting tool calls
        result = await harness.run_agent(
            command=profile.command,
            prompt=task_prompt,
            env=profile.env,
            timeout=profile.timeout,
        )

        # 4. Emit task completion event
        await harness.emit_task_event(result)

        return SpawnResult(
            exit_code=result.exit_code,
            output=result.output,
            session_id=session_id,
        )
```

### 4.2 New: `Harness` class (claw-aie)

File: `claw-aie/aie_integration/harness.py`

Wraps an agent CLI subprocess, parses its output for tool invocations, and routes them through the hook pipeline.

```python
class Harness:
    """Wraps an agent CLI, intercepting and instrumenting tool calls."""

    def __init__(self, workspace_root, session_id, agent_id):
        self.executor = ToolExecutor(workspace_root=workspace_root)
        self.hooks = HookRunner(self.executor)
        self.session_id = session_id
        self.agent_id = agent_id

    async def run_agent(self, command, prompt, env=None, timeout=300):
        """Launch agent CLI, parse output for tool calls, emit events."""
        # Agent CLI runs as subprocess
        # stdout is parsed for structured tool call markers
        # Each detected tool call goes through hooks → AIE emitter
        ...
```

### 4.3 Modified: ClawTeam profile schema

Extend `AgentProfile` with AIE-specific fields:

```python
class AgentProfile(BaseModel):
    # ... existing fields ...

    # New AIE fields
    aie_enabled: bool = True
    aie_harness: str = "claw-aie"  # or "raw" for legacy behavior
    tools: list[str] = Field(default_factory=lambda: [
        "bash", "file_read", "file_write", "glob", "grep"
    ])
    timeout: int = 300  # seconds per agent turn
    drift_threshold: float = 0.7  # abort if drift score exceeds
    oracle_check: bool = True  # run oracle evaluation after task
```

### 4.4 New: PreSpawn / PostSpawn hooks

Mirror the PreToolUse/PostToolUse pattern at the spawn lifecycle level:

```python
class SpawnHook(ABC):
    async def pre_spawn(self, profile, task) -> SpawnHookResult: ...
    async def post_spawn(self, profile, task, result) -> None: ...

class DriftCheckHook(SpawnHook):
    """Query AIE drift before spawning. Abort if threshold exceeded."""
    async def pre_spawn(self, profile, task):
        drift = await aidrift_scan(session_id=task.session_id)
        if drift.score > profile.drift_threshold:
            return SpawnHookResult(abort=True, reason=f"Drift {drift.score} > {profile.drift_threshold}")
        return SpawnHookResult(abort=False)

class OracleEvalHook(SpawnHook):
    """Run oracle evaluation after task completion."""
    async def post_spawn(self, profile, task, result):
        report = await aieval_oracle(session_id=task.session_id)
        await emit_oracle_event(report)
```

### 4.5 New: Browser Review Agent Profile

File: `~/.clawteam/profiles/browser-review.json`

```json
{
  "description": "Headless browser review agent — Playwright-based visual + functional review",
  "agent": "claw-aie",
  "command": ["claw-aie", "run", "--profile", "browser-review"],
  "model": "claude-sonnet-4-20250514",
  "aie_enabled": true,
  "tools": [
    "bash",
    "file_read",
    "file_write",
    "browser_navigate",
    "browser_screenshot",
    "browser_click",
    "browser_fill",
    "browser_console",
    "browser_assert"
  ],
  "timeout": 600,
  "drift_threshold": 0.7,
  "oracle_check": true
}
```

### 4.6 New: Browser tools (claw-aie)

File: `claw-aie/aie_integration/browser_tools.py`

```python
class BrowserTools:
    """Playwright-based browser tools for visual + functional review."""

    async def navigate(self, url: str) -> ToolResult:
        """Navigate to URL, wait for load, emit tool_call event."""

    async def screenshot(self, selector: str | None = None, full_page: bool = False) -> ToolResult:
        """Capture screenshot, save to workspace, emit event."""

    async def click(self, selector: str) -> ToolResult:
        """Click element, emit event."""

    async def fill(self, selector: str, value: str) -> ToolResult:
        """Fill form field, emit event."""

    async def console_errors(self) -> ToolResult:
        """Return all console errors from current page."""

    async def assert_visible(self, selector: str, text: str | None = None) -> ToolResult:
        """Assert element is visible (optionally with text). Returns pass/fail."""

    async def assert_no_console_errors(self) -> ToolResult:
        """Assert no console errors on current page."""

    async def accessibility_scan(self) -> ToolResult:
        """Run basic a11y checks (contrast, alt text, aria labels)."""
```

## 5. Event Flow (Complete)

```
1. ClawTeam assigns task → ownership change
2. AIEBackend.pre_spawn() → DriftCheckHook queries AIE
   ├─ drift OK → proceed
   └─ drift HIGH → abort, alert, don't spawn
3. Harness launches agent CLI with task prompt
4. Agent executes, calls tools:
   ├─ browser_navigate("http://localhost:3000")
   │   └─ PreToolUse → AIEEventEmitter → ailogger.sock (status: pending)
   │   └─ Execute → Playwright navigates
   │   └─ PostToolUse → AIEEventEmitter → ailogger.sock (status: success)
   ├─ browser_screenshot(full_page=true)
   │   └─ ... same hook pipeline ...
   ├─ browser_assert(selector="#submit", text="Submit")
   │   └─ ... same hook pipeline ...
   └─ browser_console()
       └─ ... same hook pipeline ...
5. Agent finishes → Harness collects results
6. AIEBackend.post_spawn() → OracleEvalHook runs evaluation
7. Task marked complete (or failed) in ClawTeam task store
8. Sidecar emits delegation event if task reassigned
```

## 6. Implementation Phases

| Phase | Deliverable | Status |
|---|---|---|
| **A** | Browser tools (Playwright wrapper) | ✅ Done (2026-04-16) |
| **B** | `Harness` class wrapping agent CLI subprocess | ✅ Done (2026-04-16) |
| **C** | `AIESpawnBackend` for ClawTeam integration | ✅ Done (2026-04-16) |
| **D** | Spawn hooks (DriftCheck, OracleEval) | ✅ Done (2026-04-16) |
| **E** | Browser review agent profile + end-to-end test | ✅ Done (2026-04-16) |
| **F** | ClawTeam profile schema extension (PR upstream) | ✅ Done (2026-04-16) |
| **G** | AIE lobster heartbeat cron (drift + oracle + Discord alert) | ✅ Done (2026-04-17) |

## 7. Dependencies

- `playwright` (pip) — for browser tools
- `claw-aie` existing hook infrastructure
- `clawteam` >= 0.2.0 (no changes needed to core — backend is pluggable)
- AIE logger running (`ailogger serve`)

## 8. File Map

```
claw-aie/
├── aie_integration/
│   ├── browser_tools.py         # NEW — Playwright tool implementations
│   ├── harness.py               # NEW — Agent CLI wrapper
│   ├── spawn_backend.py         # NEW — ClawTeam AIEBackend
│   ├── spawn_hooks.py           # NEW — DriftCheck, OracleEval spawn hooks
│   ├── tool_executor.py         # EXISTING — extend with browser tools
│   ├── hooks/
│   │   ├── runner.py            # EXISTING
│   │   └── aie_emitter.py       # EXISTING
│   └── ...
├── profiles/
│   └── browser-review.json      # NEW — browser review agent profile
├── INTEGRATION_SPEC.md          # THIS FILE
└── SPEC.md                      # EXISTING
```

---

## Completed (2026-04-17)

All phases A-G are complete as of 2026-04-17:

- ✅ Phase A — `browser_tools.py` exists, 8 tools implemented, 9 e2e tests passing
- ✅ Phase B — `harness.py` exists, wraps agent CLI subprocess, intercepts tool calls
- ✅ Phase C — `spawn_backend.py` exists, `AIESpawnBackend` implements ClawTeam `SpawnBackend` ABC
- ✅ Phase D — `spawn_hooks.py` exists, `DriftCheckHook`, `OracleEvalHook`, `SessionLogHook`
- ✅ Phase E — `test_e2e_browser_review.py` passes with 9 tests, 91 tests total
- ✅ Phase F — ClawTeam profile schema extended via `aie_integration/config.py`
- ✅ Phase G — AIE lobster heartbeat cron scheduled every 6h (Sydney): drift_scan → check_drift → oracle_batch → check_oracle → alert_and_halt → observability_summary → health_check. Alerts via `openclaw message` to `#evaluator-alerts`

**91 tests passing** as of 2026-04-17 push to `github.com/ChonSong/claw-aie`
