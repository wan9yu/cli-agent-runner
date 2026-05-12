# Plugin Authoring

agent-runner extends via setuptools `entry_points`. Each extension point is a
separate group; plugins declare entries in their `pyproject.toml` and are
discovered automatically at package import when installed alongside
`cli-agent-runner`.

Plugins run in the supervisor process, not inside the agent. This is intentional:
plugin code is observability/coordination glue, not workflow logic.

## Entry-points groups (0.1.3)

| Group | Purpose | Available in |
|---|---|---|
| `agent_runner.event_kinds` | Register custom event kind names | 0.1.3+ |

Additional groups for hooks, context enrichers, post-round hooks, and custom
detectors are reserved for 0.1.4 and 0.1.5.

## Registering a custom event kind (§3.1)

```toml
# my_plugin/pyproject.toml
[project.entry-points."agent_runner.event_kinds"]
my_workflow_stage_advanced = "my_plugin.events:_register"
```

```python
# my_plugin/events.py
from agent_runner.events import register_event_kind

STAGE_ADVANCED = "my_workflow_stage_advanced"


def _register() -> None:
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
current_branch = "my_plugin.enrichers:_register"
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


def _register() -> None:
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
  "schema_version": "1.2",
  "plugins": {
    "event_kinds": [...],
    "context_enrichers": ["current_branch"]
  },
  ...
}
```

## Custom monitor detectors (§3.3)

0.1.5 adds a fourth extension point — plugin authors can ship custom monitor
detectors that run alongside the 9 builtins on every monitor poll.

### Group + Protocol

```toml
[project.entry-points."agent_runner.detectors"]
my_detector = "my_plugin.detectors:_register"
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


def _register() -> None:
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
  "schema_version": "1.3",
  "plugins": {
    "event_kinds": [...],
    "context_enrichers": [...],
    "detectors": ["my_detector"]
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
