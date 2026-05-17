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
    agent_name: str | None
```

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

### `claude_error_detector` (0.1.23+, formerly `claude_rate_limit_detector`)

**Entry-point group:** `agent_runner.post_round_hooks`
**Module:** `agent_runner.builtin_plugins.claude_rate_limit`
**Old name:** `claude_rate_limit_detector` retained as an alias in
`pyproject.toml` so `[plugins] disable = ["claude_rate_limit_detector"]`
still works for back-compat.

After each round, scans the last 50 lines of the round's JSONL log for
transient errors and usage data:

- A `rate_limit_event` message with `status: "rejected"` and
  `rateLimitType: "five_hour"` (account 5h quota), or
- A result with `is_error: true` and `api_error_status` in
  {429, 500, 502, 503, 504, 408}.

When a transient error is detected, emits a `transient_error_detected`
event with `classification` ∈ {`rate_limit_account`, `rate_limit_model`,
`api_transient_5xx`, `api_timeout`}, plus `agent`, `reset_at_epoch`,
`round_num`, `raw` (≤200 chars). For `rate_limit_account` only, a legacy
`rate_limit_rejected` event is also dual-emitted for pre-0.1.23 consumers.

Per round (regardless of error state), also emits `agent_usage_recorded`
with token/cost/duration data extracted from the claude result event —
see `docs/migrations/0.1.28.md` for the full 12-field schema. The
supervisor reads `transient_error_detected` on the next dispatch cycle
and applies the configured `transient_error_action` (default `back_off`;
`rate_limit_action` retained as a deprecated alias).

No configuration required to enable the detector; it activates for any
project using claude as the agent CLI.

Non-claude agents: the detector returns early when `ctx.agent_name != "claude"`.
Third-party plugin authors may use the same `register_post_round_hook` API
to ship equivalent detectors for other agent CLIs — the bundled
`gemini_error_detector` is a working reference.

## Custom monitor detectors (§3.3)

0.1.5 adds a fourth extension point — plugin authors can ship custom monitor
detectors that run alongside the 10 builtins on every monitor poll.

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

## DetectorHelpers

`agent_runner.detector_helpers` codifies three production-tested heuristic
patterns. Use them in your detector's `detect()` to avoid false positives.

### `cumulative_window_check(events, *, kind, window_s, min_count)`

```python
from agent_runner.detector_helpers import cumulative_window_check

class CommitsStalledDetector:
    name = "commits_stalled"
    severity = "warning"
    auto_action = "none"

    def detect(self, state):
        no_commits = not cumulative_window_check(
            state.recent_events, kind="commit", window_s=3600, min_count=1
        )
        if no_commits:
            return Alert(...)
        return None
```

**Codifies the lesson:** snapshot-time `since` queries miss boundary events
due to wall-clock skew between supervisor host and storage. Cumulative
counting from explicit `ts` fields is robust.

### `dual_source_silence(scheduler_log, round_log, threshold_s)`

```python
from agent_runner.detector_helpers import dual_source_silence

class HangDetector:
    name = "hang"
    severity = "critical"
    auto_action = "none"

    def detect(self, state):
        if state.current_round is None:
            return None
        if dual_source_silence(
            state.current_round.log_path.parent.parent / "scheduler.log",
            state.current_round.log_path,
            threshold_s=600,
        ):
            return Alert(...)
        return None
```

**Codifies the lesson:** single-source silence on the scheduler log fires
false "log silent" alerts during legitimately long rounds (scheduler.log
only writes on round boundaries). Both logs must be stale before alerting.

### `phase_filter(state, *, exclude_phases)`

```python
from agent_runner.detector_helpers import phase_filter

class NoCommitsDetector:
    name = "no_commits"
    severity = "warning"
    auto_action = "none"

    def detect(self, state):
        if not phase_filter(state, exclude_phases={"retro", "review"}):
            return None  # this phase legitimately produces zero commits
        # ... actual detection logic
        return None
```

**Codifies the lesson:** "0 commits in N rounds" detectors mis-fire on
retrospective/reflection phases that intentionally produce zero commits.
Pass the set of phase names where the detector should NOT run.

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
