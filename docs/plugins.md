# Plugin Authoring

agent-runner extends via setuptools `entry_points`. Each extension point is a
separate group; plugins declare entries in their `pyproject.toml` and are
discovered automatically at package import when installed alongside
`cli-agent-runner`.

Plugins run in the supervisor process, not inside the agent. This is intentional:
plugin code is observability/coordination glue, not workflow logic.

## Trust boundary

Plugins load via setuptools entry_points at supervisor import time and run in the
supervisor's Python process with full access to its environment, filesystem, and
network. There is no sandbox. Treat `pip install <agent-runner-plugin>` with the
same trust you give any pip install — a malicious plugin can do anything the
supervisor user can do.

`auto_action="stop_service"` from plugin detectors is gated separately via
`cfg.monitor.auto_stop_on` (allow-list); plugins cannot self-elevate to
auto-stop.

## Entry-points groups

> **Entry-point semantics:** agent-runner imports the target module when it
> loads a plugin. It does **not** call the target as a function — registration
> must happen as a module-top side effect (the `register_*` call at module
> level). A `def _register():` wrapper around the call will NOT fire; the
> loader only imports.

| Group | Purpose | Available in |
|---|---|---|
| `agent_runner.event_kinds` | Register custom event kind names | 0.1.3+ |
| `agent_runner.pre_round_hooks` | Run logic before each agent round | 0.1.4+ |
| `agent_runner.context_enrichers` | Inject namespaced fields into round-context | 0.1.4+ |
| `agent_runner.post_round_hooks` | Run logic after each agent round | 0.1.4+ |
| `agent_runner.detectors` | Ship custom monitor detectors | 0.1.5+ |
| `agent_runner.serve_startup_hooks` | Run once per serve boot, before the round loop | 0.1.14+ |
| `agent_runner.dirty_handler_hooks` | Own the dirty-tree policy after a clean-exit round | 0.2.0+ |

Plugin-owned VCS paths (the `register_plugin_owned_paths()` API added in
0.1.8) are not an entry-point group — see [Declaring plugin-owned paths](#declaring-plugin-owned-paths-018) below.

## Registering a custom event kind (§3.1)

```toml
# my_plugin/pyproject.toml
[project.entry-points."agent_runner.event_kinds"]
my_workflow_stage_advanced = "my_plugin.events"
```

```python
# my_plugin/events.py
from agent_runner.events import register_event_kind

STAGE_ADVANCED = "my_workflow_stage_advanced"

# Module-top side effect: entry_point load imports this module, which
# triggers the registration.
register_event_kind(STAGE_ADVANCED, source="my-plugin@1.0")
```

After installation, the registered kind:

- Passes `events.emit()` validation in plugin code
- Surfaces in `agent-runner peek --json` under `plugins.event_kinds`
- Round-trips through the JSONL event log just like a built-in kind

## Conflict handling

- A name that collides with a built-in event kind raises `ValueError` on `register_event_kind` call
- Two plugins registering the same name from different sources raises `ValueError`
- The same source re-registering its own name is idempotent (no-op) — safe under repeated package imports

## Failure isolation

If a plugin's entry point fails to import (broken plugin module, missing dependency, etc.),
the supervisor logs a `UserWarning` and continues. A broken plugin must never crash core.

## Pre/Post round hooks + context enrichers (§3.2)

0.1.4 adds three Protocol-typed extension points loaded from these entry_points groups:

| Group | Protocol | Called |
|---|---|---|
| `agent_runner.pre_round_hooks` | `PreRoundHook` | after lock acquired, before round-context written |
| `agent_runner.context_enrichers` | `ContextEnricher` | between base context assembly and prompt write |
| `agent_runner.post_round_hooks` | `PostRoundHook` | after agent exits, before `round_end` event |

All three receive a `HookContext`:

```python
@dataclass(frozen=True)
class HookContext:
    work_dir: Path
    log_dir: Path
    project: str
    round_num: int
    phase: str | None
    agent_name: str | None       # cosmetic name from [agent].name TOML
    agent_binary: str | None     # 0.1.30+: basename of agent.command[0]
    # plus dry_run, anomaly_repetitive_*, agent_log_path — see source for full set
```

For capability detection (e.g. "is this round running claude?"), plugins
should check `ctx.agent_binary == "claude"`, NOT `ctx.agent_name`. The
former is the actual binary basename; the latter is user-cosmetic and
may be overridden in `[agent] name = "..."` (this was a real bug fixed
in 0.1.30 — strict `agent_name` check silently suppressed events when
operators set custom names).

`PostRoundHook` additionally receives a `RoundResult` (`from agent_runner.api_types import RoundResult`).
Its field set is stable across 0.1.x (additions only).

### End-to-end ContextEnricher example

```toml
# my_plugin/pyproject.toml
[project.entry-points."agent_runner.context_enrichers"]
current_branch = "my_plugin.enrichers"
```

```python
# my_plugin/enrichers.py
import subprocess
from agent_runner.hooks import HookContext, register_context_enricher


class CurrentBranchEnricher:
    name = "current_branch"

    def enrich(self, ctx: HookContext) -> dict:
        out = subprocess.run(
            ["git", "-C", str(ctx.work_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        return {"branch": out.stdout.strip() or "(detached)"}


# Module-top side effect: entry_point load imports this module, which
# triggers the registration.
register_context_enricher(CurrentBranchEnricher())
```

After installation, each round's `round-context.json` gains a `current_branch` key:

```json
{
  "round_num": 42,
  "started_at": "...",
  "current_branch": {"branch": "main"}
}
```

The agent reads it just like any other context field. Other enrichers' slices live under
their own `name` keys — collisions are structurally impossible because the runner
stitches each return value to `base_context[enricher.name]`.

### Failure isolation

Any exception raised by a hook is caught by the runner and emitted as a built-in
`hook_failed` event with:

```json
{
  "event": "hook_failed",
  "hook_name": "<plugin's name attribute>",
  "hook_kind": "pre_round | context_enricher | post_round",
  "error_type": "<exception class>",
  "error_message": "<str(exc)>",
  "traceback": "<head 1KB + ... [truncated] ... + tail 1KB>"
}
```

(Fields emitted by the `HOOK_FAILED` path in `runner.py` + `_summarize_error` in `hooks.py`.)

The round itself continues — a broken plugin must not crash the supervisor.

### What `plugin_context_enrichers()` surfaces

`peek --json` reports currently-installed enricher names under `plugins.context_enrichers`:

```json
{
  "schema_version": "1.9",
  "plugins": {
    "event_kinds": [...],
    "context_enrichers": ["current_branch"],
    "pre_round_hooks": [...],
    "post_round_hooks": [...],
    "detectors": [...],
    "owned_paths": [...]
  },
  ...
}
```

### Serve-startup hooks

Fires once per `agent-runner serve` invocation, after config load, before the
supervisor loop. Use for seeding state that subsequent rounds depend on.

Protocol:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ServeStartupHook(Protocol):
    name: str
    def __call__(self, cfg: Config) -> None: ...
```

Registration (in your plugin package's `__init__.py`):

```python
from agent_runner.hooks import register_serve_startup_hook

class MySeederHook:
    name = "my_seeder"
    def __call__(self, cfg):
        seed_path = cfg.runtime.work_dir / ".my-plugin-state"
        if not seed_path.exists():
            seed_path.write_text(_default_state())

register_serve_startup_hook(MySeederHook())
```

Entry point declaration (in your plugin's `pyproject.toml`):

```toml
[project.entry-points."agent_runner.serve_startup_hooks"]
my_seeder = "my_plugin_pkg"
```

### Failure semantics

If your hook raises, `agent-runner serve` aborts with exit code 1 and emits a
`serve_startup_hook_failed` event (best-effort). This is by design: hooks are
plugin contracts. Failing fast and loudly beats subsequent rounds failing in
hard-to-diagnose ways.

Make hooks idempotent — they may fire multiple times during a serve restart
cycle. Check for existing state before seeding.

### Round subprocess env contract

`agent-runner serve` injects three environment variables into the round
subprocess (which then propagates to the agent CLI):

| Variable | Value |
|---|---|
| `AGENT_RUNNER_LOG_DIR` | Absolute path to `runtime.log_dir`. Use to construct paths to `events-*.jsonl`, `narrative.md`, `.agent-done` sentinel, etc. |
| `AGENT_RUNNER_ROUND_NUM` | Current round number as string (matches `round_num` field in events.jsonl). |
| `AGENT_RUNNER_PHASE` | Current phase name from rotation, or `""` (empty string) when no `[phases]` section is configured. |

Example (bash):

```bash
echo "starting R$AGENT_RUNNER_ROUND_NUM phase=$AGENT_RUNNER_PHASE"
echo "done: round complete" > "$AGENT_RUNNER_LOG_DIR/.agent-done"
```

These contracts are stable; agents in any language / framework can rely
on them.

## Built-in post_round_hooks

agent-runner ships two built-in `post_round_hooks` plugins registered
automatically via their own entry-points: `claude_error_detector` (below)
and `gemini_error_detector` (0.1.24+, parallel for gemini CLI).

### `claude_error_detector` (0.1.23+)

**Entry-point group:** `agent_runner.post_round_hooks`
**Module:** `agent_runner.builtin_plugins.claude_rate_limit`

Renamed from `claude_rate_limit_detector` in 0.1.23 when the detector
was generalized from single-rate-limit to multi-classification. The
old-name alias was kept as a `pyproject.toml` entry-point through 0.1.34
and removed in 0.1.35. Operators still using `[plugins] disable =
["claude_rate_limit_detector"]` must switch to `claude_error_detector`.

After each round, scans the last 50 lines of the round's JSONL log for
transient errors and usage data:

- A `rate_limit_event` message with `status: "rejected"` and
  `rateLimitType: "five_hour"` (account 5h quota), or
- A result with `is_error: true` and `api_error_status` in
  {429, 500, 502, 503, 504, 529, 408}.

When a transient error is detected, emits a `transient_error_detected`
event with `classification` ∈ {`rate_limit_account`, `rate_limit_model`,
`api_transient_5xx`, `api_timeout`}, plus `agent`, `reset_at_epoch`,
`round_num`, `raw` (≤200 chars).

Per round (regardless of error state), also emits `agent_usage_recorded`
with token/cost/duration data extracted from the claude result event —
see `docs/migrations/0.1.28.md` for the full payload schema. The
supervisor reads `transient_error_detected` on the next dispatch cycle
and applies the configured `transient_error_action` (default `back_off`).

No configuration required to enable the detector; it activates for any
project using claude as the agent CLI.

Non-claude agents: the detector returns early when `ctx.agent_binary != "claude"`.
Third-party plugin authors may use the same `register_post_round_hook` API
to ship equivalent detectors for other agent CLIs — the bundled
`gemini_error_detector` is a working reference.

## Custom monitor detectors (§3.3)

0.1.5 adds a fourth extension point — plugin authors can ship custom monitor
detectors that run alongside the 12 builtins on every monitor poll.

### Group + Protocol

```toml
[project.entry-points."agent_runner.detectors"]
my_detector = "my_plugin.detectors"
```

```python
# my_plugin/detectors.py
from agent_runner.api_types import Alert, ProjectState
from agent_runner.monitor import register_detector


class MyDetector:
    name = "my_detector"
    severity = "warning"     # "info" | "warning" | "critical"
    auto_action = "none"     # "none" | "stop_service"

    def detect(self, state: ProjectState) -> Alert | None:
        if not _should_fire(state):
            return None
        return Alert(
            severity=self.severity,
            detector=self.name,
            message="something is off",
            context={"hint": "look here"},
            ts="...",  # use events.now_iso_ms()
            auto_action=self.auto_action,
        )


# Module-top side effect: entry_point load imports this module, which
# triggers the registration.
register_detector(MyDetector())
```

`Detector` is a `@runtime_checkable` Protocol — `isinstance(obj, Detector)` returns
True for any class with the four required attributes (`name`, `severity`,
`auto_action`, `detect`).

### Auto-stop opt-in

A plugin detector that returns alerts with `auto_action="stop_service"` will
NOT actually stop the supervisor unless its `name` appears in
`cfg.monitor.auto_stop_on`. Operators must opt plugin detectors in explicitly:

```toml
# agent-runner.toml
[monitor]
auto_stop_on = ["oauth_fail", "disk_critical", "my_detector"]
```

The default `auto_stop_on` includes only the two built-in critical detectors
(`oauth_fail`, `disk_critical`). This prevents a buggy or aggressive plugin
detector from stopping production services without explicit operator consent.

### Failure isolation

If `detect(state)` raises an exception, the runner logs a `UserWarning` and
the remaining detectors continue. No alert is emitted for the crashing
detector. Other plugin detectors and all builtins still run normally.

### `peek --json` surface

```json
{
  "schema_version": "1.9",
  "plugins": {
    "event_kinds": [...],
    "context_enrichers": [...],
    "pre_round_hooks": [...],
    "post_round_hooks": [...],
    "detectors": ["my_detector"],
    "owned_paths": [...]
  },
  ...
}
```

### Worked example: project-specific monitor detector with plugin-emitted exempt flag

This example shows the full pattern for a project-specific monitor detector
that filters out rounds the plugin marks as exempt by some project rule.
Covers gateway-class needs like "stuck role detection" (count git commits per
round) and "wall-time trend" (compare recent avg vs older avg).

**The shape**: a plugin emits a custom event for exempt rounds; a custom
monitor detector reads recent events, builds the exempt set, and applies its
own detection logic only on non-exempt rounds.

**Plugin file** (`myproject_agent_plugin/__init__.py`):

```python
"""Example project-specific plugin: detect stuck rounds, exempt short rounds."""

from __future__ import annotations

from agent_runner.api_types import Alert, ProjectState
from agent_runner.events import emit, now_iso_ms, register_event_kind
from agent_runner.hooks import HookContext, register_post_round_hook
from agent_runner.monitor import register_detector

# Register the kind this plugin emits (events.py invariant). The stuck signal is
# an Alert, not an event — detectors return Alerts and register no kind.
register_event_kind("myproject_round_exempt", source="myproject_agent_plugin")


class _ExemptHook:
    """PostRoundHook that flags short rounds as exempt from stuck detection."""

    name = "myproject_exempt"

    def after_round(self, ctx: HookContext, result) -> None:
        # Project rule: rounds under 60s are exempt (e.g. nothing-to-do iters)
        if result.duration_s < 60:
            emit(ctx.log_dir, "myproject_round_exempt",
                 round_num=ctx.round_num, reason="short_round")


class _StuckRoundDetector:
    """3+ consecutive non-exempt rounds with no commit -> stuck."""

    name = "myproject_stuck_round"
    severity = "warning"
    auto_action = "none"

    def detect(self, state: ProjectState) -> Alert | None:
        exempt = {
            e["round_num"] for e in state.recent_events
            if e.get("event") == "myproject_round_exempt"
        }
        rounds_ended = [
            e for e in state.recent_events
            if e.get("event") == "round_end" and e.get("round_num") not in exempt
        ]
        if len(rounds_ended) < 3:
            return None
        nums = sorted({e["round_num"] for e in rounds_ended[-3:]})

        # Project-specific: real plugin would shell to `git log --grep`
        if sum(_count_commits_for_rounds(nums).values()) > 0:
            return None
        return Alert(
            severity=self.severity,
            detector=self.name,
            message=f"3 non-exempt rounds (round_nums={nums}) produced no commits",
            context={"round_nums": nums},
            ts=now_iso_ms(),
            auto_action=self.auto_action,
        )


def _count_commits_for_rounds(round_nums: list[int]) -> dict[int, int]:
    """Stub: real plugin would shell out to `git log` or read a sidecar file."""
    return {n: 0 for n in round_nums}


register_post_round_hook(_ExemptHook())
register_detector(_StuckRoundDetector())
```

**Plugin registration** (`pyproject.toml`):

```toml
[project.entry-points."agent_runner.post_round_hooks"]
myproject_exempt = "myproject_agent_plugin:_ExemptHook"

[project.entry-points."agent_runner.detectors"]
myproject_stuck = "myproject_agent_plugin:_StuckRoundDetector"
```

**The pattern in 3 lines**:

1. PostRoundHook emits `<plugin>_round_exempt` events for exempt rounds.
2. A `Detector` reads `state.recent_events`, builds the exempt set, filters out
   exempt rounds, applies its own logic.
3. `detect(state)` returns an `Alert` when the condition fires, else `None`.

The same shape covers other project-specific signals: count git commits per
round (no-commit-rounds-stuck detection), avg recent vs older round
duration (wall-time-trend detection for context bloat), etc. Project-specific
semantics live in the plugin — agent-runner core stays agent-agnostic.

(Adjust import paths, function names, and exact API surface to match what
your codebase exposes — this is a template, not a literal must-compile snippet.)

## DirtyHandler — custom dirty-tree policy (0.2.0+)

0.2.0 adds a fourth lifecycle-hook extension point: `DirtyHandler`. Plugins
that register on this group take over what happens when a round exits cleanly
but leaves the working tree dirty.

The bundled `default_dirty_handler` plugin ships enabled (priority 1000) and
implements the existing `stash` / `ignore` / `auto_commit` behavior driven by
`[vcs] dirty_action`. Operators who want a different policy disable the default
and register their own handler.

### Protocol

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class DirtyHandler(Protocol):
    name: str
    priority: int  # ascending; lower runs first; bundled default = 1000

    def handle_dirty(
        self,
        ctx: HookContext,
        dirty_files: list[str],
    ) -> "DirtyOutcome | None": ...
```

Handlers are invoked in ascending `priority` order (ties: registration order).
The first to return a non-`None` `DirtyOutcome` wins; dispatch stops. A handler
that raises is isolated via `hook_failed` (`hook_kind="dirty_handler"`) and
treated as `None` (pass to the next handler).

### `DirtyOutcome`

```python
from agent_runner.api_types import DirtyOutcome

DirtyOutcome(kind="ignored")                    # left dirty intentionally
DirtyOutcome(kind="stashed", ref="<stash-sha>")
DirtyOutcome(kind="committed", ref="<commit-sha>")
```

### Minimal override recipe

**1. Disable the bundled default:**

```toml
# agent-runner.toml
[plugins]
disable = ["default_dirty_handler"]
```

**2. Declare the entry point:**

```toml
# your_plugin/pyproject.toml
[project.entry-points."agent_runner.dirty_handler_hooks"]
my_dirty_handler = "your_plugin.dirty:MyDirtyHandler"
```

**3. Implement and register:**

```python
# your_plugin/dirty.py
from agent_runner.api_types import DirtyOutcome
from agent_runner.hooks import HookContext, register_dirty_handler


class MyDirtyHandler:
    name = "my_dirty_handler"
    priority = 10  # ascending; only matters when multiple handlers coexist

    def handle_dirty(self, ctx: HookContext, dirty_files) -> DirtyOutcome | None:
        # Return None to pass to the next handler.
        # Return a DirtyOutcome to claim the result and stop dispatch.
        return DirtyOutcome(kind="ignored")


# Module-top side effect — fires at entry_point load time.
register_dirty_handler(MyDirtyHandler())
```

### Using core git primitives

Handlers may call the public `api` primitives:

```python
from agent_runner import api

# Stash — returns a StashRef (with .sha) or None if nothing to stash
ref = api.stash_orphan(
    ctx.work_dir,
    round_num=ctx.round_num,
    phase=ctx.phase,
    idempotency_s=ctx.vcs.stash_idempotency_s if ctx.vcs else 5,
    log_dir=ctx.log_dir,
)
outcome = DirtyOutcome(kind="ignored") if ref is None else DirtyOutcome(kind="stashed", ref=ref.sha)

# Auto-commit — returns commit SHA or "" (nothing staged); raises AutoCommitError on failure
sha = api.try_auto_commit(ctx.work_dir, ctx.round_num, ctx.phase, log_dir=ctx.log_dir)
```

`ctx.vcs` exposes `dirty_action` and `stash_idempotency_s` from `[vcs]` config
(populated by the runner when dispatching dirty handlers; `None` in other contexts).

### `DirtyOutcome` on `RoundResult`

`RoundResult.dirty_outcome: DirtyOutcome | None` (0.2.0+) carries whatever the
winning handler returned. `PostRoundHook` authors can read it:

```python
def after_round(self, ctx, result):
    if result.dirty_outcome and result.dirty_outcome.kind == "committed":
        # A handler auto-committed. result.dirty_outcome.ref is the SHA.
        ...
```

## Declaring plugin-owned paths (0.1.8+)

If your plugin writes files inside the supervisor's `work_dir`
(audit memos, generated reports, plugin-local state, etc.), declare them
so the orphan-stash defense doesn't silently sweep them into a stash
between rounds.

```python
# my_plugin/__init__.py
from agent_runner.vcs_state import register_plugin_owned_paths

# Module-top side effect — must register before the first round runs.
register_plugin_owned_paths([
    "proposals/",                  # trailing slash → prefix match
    "logs/plugins/my_plugin/**/*", # recursive glob (fnmatch)
    "reports/*.md",                # single-segment glob (PurePath.match)
])
```

### Matching semantics

| Pattern | Matches | Notes |
|---|---|---|
| `"proposals/"` | `proposals`, `proposals/foo.md`, `proposals/sub/bar.md` | Trailing `/` → prefix match. |
| `"proposals"` (no slash) | `proposals` exactly | Single-segment literal. |
| `"reports/*.md"` | `reports/dev.md` | `*` does not cross slashes. |
| `"reports/**/*.md"` | `reports/dev.md`, `reports/sub/qa.md` | `**` matches across directory separators. |
| `"logs/plugins/**/*"` | `logs/plugins/argus/state.json` | Same — `**` covers intermediate dirs. |

### Caveat — this is NOT a "make work_dir messy" license

Plugin-owned paths express *"these are the plugin's expected deliverable
files; do not stash them"*. They are not permission to scatter scratch
files. Operator owns cleanup of these paths.

If your plugin writes ephemeral state that should be cleaned up between
rounds, do the cleanup yourself in a `PostRoundHook` — don't rely on
the orphan-stash defense to sweep it.

### Visibility

`agent-runner peek --select plugins.owned_paths` shows the currently
registered list (peek schema v1.5+).

## Plugin tests + consumer pytest collision

Consumer projects often have their own `tests/` directory. If your plugin
also has tests (e.g. `tools/my_agent_plugin/tests/`), pytest's testpaths
walk can find both and fail with `ModuleNotFoundError` when the same
package name lives in two locations.

Two recommended patterns:

### Pattern A — plugin tests inside the plugin package

Plugin author owns this:

```
my_agent_plugin/
├── __init__.py
├── core.py
└── tests/
    ├── __init__.py
    └── test_core.py
```

In `my_agent_plugin/pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["my_agent_plugin/tests"]
```

This scopes pytest collection to your plugin's tests when running locally.

### Pattern B — consumer ignores your plugin in their pytest config

Consumer owns this:

```toml
# In the consumer project's pytest.ini or pyproject.toml:
[tool.pytest.ini_options]
addopts = ["--ignore=tools/my_agent_plugin"]
```

Both work. Pattern A is preferable for plugin authors (no consumer
configuration needed); Pattern B is for cases where the consumer integrates
a plugin they don't own.
