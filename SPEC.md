# claw-aie — AIE-Compatible Agent Harness

> **Purpose:** A clean, observable agent harness with working tool execution and PreToolUse/PostToolUse hooks, designed as the canonical instrumentable harness for the Agent Interaction Evaluator (AIE).
>
> **Design principle:** Every consequential action passes through a hook. Every hook can emit to AIE. The harness itself is harness-agnostic — it observes, it doesn't modify agent behaviour.

---

## 1. Overview

claw-aie extends the claw-code Python routing layer with:

1. **Async tool execution** — based on the Rust `mvp_tool_specs()` patterns, implemented in Python
2. **PreToolUse / PostToolUse hooks** — working execution pipeline, not just config
3. **AIE event emission** — every hook invocation emits structured events to the AIE logger

### What This Is

A reference harness that AIE instruments. Any agent can be built on top of it, or adapted to emit events via the same hook interface.

### What This Is Not

- Not a full Claude Code clone
- Not dependent on any specific LLM provider
- Not modified claw-code — claw-code Python source is used as-is for routing; our additions are in `aie_integration/`

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          claw-aie                                    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  claw-code Python (src/) — as-is from fork                  │    │
│  │  PortRuntime.route_prompt()                                 │    │
│  │  QueryEnginePort.submit_message()                            │    │
│  │  ToolPool + tool manifests                                  │    │
│  └──────────────────────────┬───────────────────────────────────┘    │
│                             │                                        │
│  ┌──────────────────────────▼───────────────────────────────────┐    │
│  │  aie_integration/ — our additions                            │    │
│  │                                                               │    │
│  │  ┌────────────────────────────────────────────────────────┐  │    │
│  │  │  HookRunner                                            │  │    │
│  │  │  run_pre_tool_use(tool_name, tool_input) → result     │  │    │
│  │  │  run_post_tool_use(tool_name, tool_input, output)     │  │    │
│  │  └────────────────────┬────────────────────────────────────┘  │    │
│  │                     │                                           │    │
│  │  ┌──────────────────▼────────────────────────────────────┐  │    │
│  │  │  ToolExecutor                                           │  │    │
│  │  │  execute(tool_name, tool_input) → output               │  │    │
│  │  │  async def — bash, file_read, file_write, web_search  │  │    │
│  │  └────────────────────┬────────────────────────────────────┘  │    │
│  │                     │                                           │    │
│  │  ┌──────────────────▼────────────────────────────────────┐  │    │
│  │  │  AIEEventEmitter                                       │  │    │
│  │  │  emit(event_type, payload) → AILoggerClient           │  │    │
│  │  └────────────────────────────────────────────────────────┘  │    │
│  └───────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ JSON-RPC over Unix socket
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AIE (Agent Interaction Evaluator)                                   │
│  /tmp/ailogger.sock                                                 │
│                                                                       │
│  Events received: tool_call, assumption, correction, delegation     │
│  → indexed in txtai                                                  │
│  → oracle evaluation                                                 │
│  → drift detection                                                  │
│  → audit trails                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 ToolExecutor (`aie_integration/tool_executor.py`)

Async tool execution based on Rust `mvp_tool_specs()`.

```python
class ToolExecutor:
    """Async tool executor — preToolUse / postToolUse hooks run around every call."""

    async def execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        # 1. Run pre_tool_use hook
        pre_result = await self.hooks.run_pre_tool_use(tool_name, tool_input)
        if pre_result.denied:
            return ToolResult(denied=True, message=pre_result.denial_message)

        # 2. Execute tool
        result = await self._dispatch(tool_name, tool_input)

        # 3. Run post_tool_use hook
        await self.hooks.run_post_tool_use(tool_name, tool_input, result.output)

        return result
```

**MVP tools:**

| Tool | Description |
|---|---|
| `bash` | Execute shell command, return stdout/stderr |
| `file_read` | Read file contents |
| `file_write` | Write content to file |
| `file_edit` | Edit specific lines in a file |
| `glob` | Find files by pattern |
| `grep` | Search file contents |
| `web_search` | Search the web |
| `web_fetch` | Fetch URL content |

**ToolResult dataclass:**
```python
@dataclass
class ToolResult:
    tool_name: str
    output: str
    exit_code: int
    duration_ms: int
    denied: bool = False
    error: str | None = None
```

### 3.2 HookRunner (`aie_integration/hooks/runner.py`)

Working PreToolUse / PostToolUse execution pipeline.

```python
@dataclass
class HookResult:
    allowed: bool
    denied: bool = False
    denial_message: str | None = None
    messages: list[str] = field(default_factory=list)

class HookRunner:
    """PreToolUse + PostToolUse hook execution."""

    async def run_pre_tool_use(
        self, tool_name: str, tool_input: dict
    ) -> HookResult:
        """Run all pre_tool_use hooks for a tool.
        
        Each hook can:
        - Allow: continue execution
        - Deny: block with message
        - Mutate: modify tool_input before execution
        
        Returns: HookResult with final decision
        """

    async def run_post_tool_use(
        self, tool_name: str, tool_input: dict, output: str
    ) -> None:
        """Run all post_tool_use hooks after tool execution."""
```

**Hook interface:**
```python
class ToolHook(ABC):
    """Base class for tool hooks."""
    
    async def pre_tool_use(
        self, tool_name: str, tool_input: dict
    ) -> HookResult | None:
        """Return None = allow. Return HookResult to override."""
        return None
    
    async def post_tool_use(
        self, tool_name: str, tool_input: dict, output: str
    ) -> None:
        """Post-execution hook. Cannot block."""
        pass
```

**Built-in hooks (all in `aie_integration/hooks/`):**
- `permission_hook.py` — tool permission enforcement
- `rate_limit_hook.py` — rate limiting per tool
- `aie_emitter_hook.py` — emits to AIE logger ← **this is our integration point**

### 3.3 AIEEventEmitter (`aie_integration/hooks/aie_emitter.py`)

```python
class AIEEventEmitter(ToolHook):
    """Emits structured events to the AIE logger via Unix socket."""

    def __init__(self, socket_path: str = "/tmp/ailogger.sock"):
        self.client = AILoggerClient(socket_path=socket_path)

    async def pre_tool_use(self, tool_name: str, tool_input: dict) -> HookResult | None:
        event = build_tool_call_event(
            tool_name=tool_name,
            tool_input=sanitise(tool_input),  # no secrets
            trigger="explicit_request",
            outcome_status="pending",
        )
        await self.client.emit(event)
        return None  # always allow — AIE observes, doesn't block

    async def post_tool_use(
        self, tool_name: str, tool_input: dict, output: str
    ) -> None:
        event = build_outcome_event(
            tool_name=tool_name,
            tool_input=sanitise(tool_input),
            output=output[:500],  # truncate for safety
            status="success" if not output.startswith("Error:") else "error",
        )
        await self.client.emit(event)
```

---

## 4. Hook Configuration

Hooks are configured via `~/.claw-aie/hooks.yaml`:

```yaml
hooks:
  pre_tool_use:
    - name: "permission"
      enabled: true
      tools: ["*"]  # all tools
    - name: "aie_emitter"
      enabled: true
      tools: ["*"]
      emit_to_aie: true

  post_tool_use:
    - name: "aie_emitter"
      enabled: true
      tools: ["*"]
      emit_to_aie: true
```

The hook system is extensible — add new hooks by creating a `ToolHook` subclass and registering it in `hooks.yaml`.

---

## 5. Directory Structure

```
claw-aie/
├── src/                         # claw-code Python (as-is from fork)
│   ├── runtime.py               # PortRuntime
│   ├── query_engine.py         # QueryEnginePort
│   ├── tool_pool.py            # ToolPool
│   └── ... (all claw-code source)
├── aie_integration/             # NEW — our additions
│   ├── __init__.py
│   ├── tool_executor.py         # ToolExecutor + ToolResult
│   ├── hooks/
│   │   ├── __init__.py
│   │   ├── runner.py           # HookRunner + HookResult
│   │   ├── base.py              # ToolHook ABC
│   │   ├── permission_hook.py   # Permission enforcement
│   │   ├── rate_limit_hook.py   # Rate limiting
│   │   └── aie_emitter.py       # AIE event emission ← integration
│   ├── sanitiser.py            # Secret stripping for tool inputs
│   ├── config.py               # hooks.yaml loader
│   └── cli.py                  # claw-aie CLI entry point
├── tests/
│   ├── test_tool_executor.py
│   ├── test_hooks.py
│   ├── test_aie_emitter.py
│   └── fixtures/
├── SPEC.md
├── README.md
└── requirements.txt
```

---

## 6. Event Schema (AIE-compatible)

All events emitted to the AIE logger use the AIE schema from `agent-interaction-evaluator/SPEC.md §3`.

### tool_call event (from aie_emitter)

```json
{
  "schema_version": "1.0",
  "event_id": "<uuid>",
  "event_type": "tool_call",
  "timestamp": "<ISO-8601>",
  "agent_id": "claw-aie",
  "session_id": "<port-session-id>",
  "interaction_context": {
    "channel": "terminal",
    "workspace_path": "<cwd>",
    "parent_event_id": null
  },
  "tool": {
    "name": "bash",
    "namespace": "claw-aie",
    "arguments": { "command": "ls -la" },
    "argument_schema": null
  },
  "trigger": {
    "type": "explicit_request",
    "triggered_by_event_id": null
  },
  "outcome": {
    "status": "success",
    "duration_ms": 42,
    "error_message": null,
    "output_summary": "total 48..."
  }
}
```

---

## 7. Dependencies

```
# Python 3.11+
aiohttp>=3.9.0           # async HTTP for web tools
httpx>=0.26.0            # async HTTP client
websockets>=12.0         # async websocket
pyyaml>=6.0              # hooks.yaml parsing
jsonschema>=4.21.0       # event validation

# Testing
pytest>=8.0.0
pytest-asyncio
pytest-aiohttp            # for HTTP mock tools

# AIE integration (assumes AIE is installed)
# The aie_integration module uses AILoggerClient from agent_interaction_evaluator
```

---

## 8. Development Phases

### Phase A — Foundation
- [ ] Project structure (`aie_integration/`, `hooks/`, `tests/`)
- [ ] `ToolExecutor` with MVP 4 tools (bash, file_read, file_write, glob)
- [ ] `ToolResult` dataclass
- [ ] `sanitiser.py` — strip secrets from tool inputs
- [ ] Basic unit tests

### Phase B — Hook System
- [ ] `ToolHook` ABC
- [ ] `HookRunner` with PreToolUse / PostToolUse execution
- [ ] `permission_hook.py` — block destructive tools
- [ ] `rate_limit_hook.py` — per-tool rate limiting
- [ ] `hooks.yaml` config loader
- [ ] Hook tests

### Phase C — AIE Integration
- [ ] `AIEEventEmitter` hook
- [ ] Connect to `ailogger.sock` (AILoggerClient from AIE)
- [ ] Emit pre + post tool_call events
- [ ] Session ID propagation from PortRuntime
- [ ] Integration tests with real AIE logger

### Phase D — CLI + Invocation
- [ ] `claw-aie` CLI entry point
- [ ] Wire `PortRuntime` → `ToolExecutor` → `HookRunner` → `AIEEventEmitter`
- [ ] End-to-end test with real tool calls + AIE indexing

---

## 9. Design Constraints

1. **AIE observes, doesn't block** — the AIE emitter hook always returns `None` (allow). It logs, it doesn't interfere.
2. **Secrets never leave the harness** — `sanitiser.py` strips `PASSWORD`, `SECRET`, `TOKEN`, `KEY`, `API_KEY` before any event is emitted.
3. **Hooks are composable** — multiple hooks can run in sequence. A denial from any pre_tool_use hook blocks execution.
4. **No external services** — AIE integration connects to the existing `ailogger.sock` from Phase 1. No new services required.
5. **Async-first** — all tool execution and hooks are async. Enables concurrent tool execution.

---

## 10. Open Questions

| # | Question | Notes |
|---|---|---|
| 1 | Should we add `assumption` and `correction` event emission? | Phase D or later — requires LLM output parsing |
| 2 | Web search tool — which provider? | TBD — could use DuckDuckGo or SERP API |
| 3 | MCP tool support? | Phase D+ — claw-code has MCP in Rust, not in Python |

---

## 11. References

- `instructkr/claw-code` (via fork) — Python routing layer source
- `claw-code/rust/crates/tools/src/lib.rs` — Rust `mvp_tool_specs()` for tool executor design
- `claw-code/rust/crates/runtime/src/hooks.rs` — Rust hook data structures
- `ChonSong/agent-interaction-evaluator` — AIE, `ailogger.sock`, event schema
