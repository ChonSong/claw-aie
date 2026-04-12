# claw-aie — AIE-Compatible Agent Harness

> Instrumented agent harness with async tool execution, PreToolUse/PostToolUse hooks, and AIE event emission.
> Part of the [ChonSong Ecosystem](https://github.com/ChonSong/ecosystem).

---

## What It Does

claw-aie is the **canonical execution layer** for the Agent Interaction Evaluator (AIE) ecosystem. Every consequential action passes through a hook pipeline, and every hook can emit structured events to AIE for indexing, drift detection, and audit trail generation.

```
Agent request
    ↓
ToolExecutor.execute(tool, input)
    ↓ HookRunner.run_pre_tool_use() ← permission, rate limit, AIE emit
    ↓ (denied? → return blocked)
    ↓ _dispatch(tool, input) → actual execution
    ↓ HookRunner.run_post_tool_use() ← AIE emit with outcome
    ↓
ToolResult
    ↓ event emitted to AIE logger via /tmp/ailogger.sock
```

## Architecture

```
claw-aie/
├── src/                          # claw-code Python routing layer (upstream, as-is)
├── aie_integration/              # Our additions
│   ├── tool_executor.py          # Async tool executor (bash, file_read, file_write, glob, grep)
│   ├── sanitiser.py              # Secret stripping for event payloads
│   ├── hooks/
│   │   ├── base.py               # ToolHook ABC
│   │   ├── runner.py             # HookRunner — PreToolUse / PostToolUse pipeline
│   │   ├── permission_hook.py    # Block destructive tools (rm -rf, system paths)
│   │   ├── rate_limit_hook.py    # Per-tool token bucket rate limiting
│   │   └── aie_emitter.py        # Emit structured events to AIE logger
│   ├── config.py                 # hooks.yaml loader
│   └── cli.py                    # CLI entry point (Phase D)
├── SPEC.md                       # Full specification
└── tests/
```

## Quick Start

```bash
# Install dependencies
pip install -e .

# Run a tool directly (Phase A — working)
PYTHONPATH=src:. python3 -c "
import asyncio
from aie_integration.tool_executor import ToolExecutor

async def main():
    executor = ToolExecutor()
    result = await executor.execute('bash', {'command': 'echo hello'})
    print(result.output)  # → hello

asyncio.run(main())
"
```

## Development Phases

| Phase | Status | Description |
|---|---|---|
| A — Foundation | ✅ Complete | ToolExecutor, ToolResult, sanitiser, 4 MVP tools |
| B — Hook System | 📋 Next | ToolHook ABC, HookRunner, permission/rate-limit hooks, config loader |
| C — AIE Integration | 📋 Planned | AIEEventEmitter hook, ailogger.sock connection, session propagation |
| D — CLI + Invocation | 📋 Planned | `claw-aie` CLI, PortRuntime wiring, e2e test |

See [SPEC.md](./SPEC.md) for detailed deliverables per phase.

## Hook Configuration

Hooks are loaded from `~/.claw-aie/hooks.yaml`:

```yaml
hooks:
  pre_tool_use:
    - name: "permission"
      enabled: true
    - name: "aie_emitter"
      enabled: true
      emit_to_aie: true
  post_tool_use:
    - name: "aie_emitter"
      enabled: true
      emit_to_aie: true
```

## Ecosystem Context

| Layer | Component | Role |
|---|---|---|
| L1 — Orchestration | [ClawTeam](https://github.com/HKUDS/ClawTeam) + [sidecar](https://github.com/ChonSong/clawteam-sidecar) | Task delegation, swarm coordination |
| **L2 — Execution** | **claw-aie** (this repo) | **Tool execution + event emission** |
| L3 — Observability | [AIE](https://github.com/ChonSong/agent-interaction-evaluator) | Drift detection, oracle evaluation, audit trails |
| L4 — Context | [RepoTransmute](https://github.com/ChonSong/repo-transmute) | Code blueprints, semantic search |

## Upstream

claw-aie extends the [claw-code](https://github.com/instructkr/claw-code) Python routing layer with AIE instrumentation. The upstream `src/` directory is used as-is; all AIE additions live in `aie_integration/`.

## License

MIT
