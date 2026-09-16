# Plugin Authoring

agent-runner extends via one setuptools `entry_points` group,
`agent_runner.plugins`. Each entry resolves to a module-level
`PLUGIN = PluginManifest(...)` that declares every capability the plugin
provides; plugins are discovered automatically at package import when
installed alongside `cli-agent-runner`.

Plugins run in the supervisor process, not inside the agent. This is intentional:
plugin code is observability/coordination glue, not workflow logic.

## Trust boundary

Plugins load via the `agent_runner.plugins` entry_points group at supervisor
import time and run in the supervisor's Python process with full access to its
environment, filesystem, and network. There is no sandbox. Treat
`pip install <agent-runner-plugin>` with the same trust you give any pip
install — a malicious plugin can do anything the supervisor user can do.

## The `agent_runner.plugins` entry-point group

> **Entry-point semantics:** agent-runner imports the target module and reads
> its declared `PLUGIN` attribute — it does **not** call anything as a
> function. Registration is data, not a side effect: the loader hands your
> `PluginManifest` to `register_manifest()`, which registers each declared
> capability into the right internal registry itself.

```toml
# my_plugin/pyproject.toml
[project.entry-points."agent_runner.plugins"]
my_plugin = "my_plugin:PLUGIN"
```

```python
# my_plugin/__init__.py
from agent_runner._plugin_manifest import PluginManifest

PLUGIN = PluginManifest(name="my_plugin", ...)
```

`PluginManifest` (`agent_runner._plugin_manifest`) is a frozen dataclass with
one field per capability family, every field optional and defaulting to
empty:

| Field | Type | Registers as |
|---|---|---|
| `name` | `str` | The plugin's own identity — `[plugins] disable` keys on this, not on any individual hook's own `.name`. |
| `post_round_hooks` | `tuple[PostRoundHook, ...]` | Runs after each agent round. |
| `cooperative_stop` | `Literal["SIGTERM", "SIGINT"] \| None` | The signal this preset's agent CLI catches to drain/wrap up when a round is stopped (`None` = no cooperative drain — the hard SIGTERM-first path). Reported under `peek --json`'s `plugins.sigterm_cooperative` (§ below). |

A plugin that provides more than one capability just fills in more than one
field on the same manifest — there is nothing to register per-capability.

## Failure isolation

If a plugin's entry point fails to import, or its `PLUGIN` attribute is missing or
malformed, the supervisor logs a `UserWarning` and continues loading the rest.
A broken plugin must never crash core.

## Post-round hooks (§3.2)

| Field | Protocol | Called |
|---|---|---|
| `post_round_hooks` | `PostRoundHook` | after agent exits, after `round_end` event |

It receives a `HookContext`:

```python
@dataclass(frozen=True)
class HookContext:
    work_dir: Path
    log_dir: Path
    project: str
    round_num: int
    phase: str | None
    agent_name: str | None  # cosmetic name from [agent].name TOML
    agent_binary: str | None  # 0.1.30+: basename of agent.command[0]
    # plus dry_run, anomaly_repetitive_*, agent_log_path — see source for full set
```

For capability detection (e.g. "is this round running claude?"), plugins
should check `ctx.agent_binary == "claude"`, NOT `ctx.agent_name`. The
former is the actual binary basename; the latter is user-cosmetic and
may be overridden in `[agent] name = "..."` (this was a real bug fixed
in 0.1.30 — strict `agent_name` check silently suppressed events when
operators set custom names).

`PostRoundHook` additionally receives a `RoundResult` (`from agent_runner.api_types import RoundResult`).
Its field set is stable across releases (additions only).

### `peek --json` surface

`peek --json` reports currently-registered post_round_hook names under
`plugins.post_round_hooks`:

> The `schema_version` shown in the `peek --json` example below is illustrative;
> the authoritative value is `PEEK_SCHEMA_VERSION` in code.

```json
{
  "schema_version": "2.6",
  "plugins": {
    "post_round_hooks": ["claude_error_detector"],
    "disabled": [],
    "sigterm_cooperative": {"claude": "SIGINT", "gemini": "SIGTERM", "pi": "SIGTERM"}
  },
  ...
}
```

### Failure isolation

Any exception raised by a hook is caught by the runner and emitted as a built-in
`hook_failed` event with:

```json
{
  "event": "hook_failed",
  "hook_name": "<plugin's name attribute>",
  "hook_kind": "post_round",
  "error_type": "<exception class>",
  "error_message": "<str(exc)>",
  "traceback": "<head 1KB + ... [truncated] ... + tail 1KB>"
}
```

(Fields emitted by the `HOOK_FAILED` path in `runner.py` + `_summarize_error` in `hooks.py`.)

The round itself continues — a broken plugin must not crash the supervisor.

### Round subprocess env contract

Four environment variables reach the agent CLI, injected in two stages:
`agent-runner serve` sets `AGENT_RUNNER_LOG_DIR` and `AGENT_RUNNER_FRESH_EYES`
on the round subprocess; the round process then sets `AGENT_RUNNER_LOG_DIR`,
`AGENT_RUNNER_ROUND_NUM` and `AGENT_RUNNER_PHASE` on the agent CLI. All four are
visible to the agent and to any hook running inside the round.

| Variable | Value |
|---|---|
| `AGENT_RUNNER_LOG_DIR` | Absolute path to `runtime.log_dir`. Use to construct paths to `events-*.jsonl`, `narrative.md`, `.agent-done` sentinel, etc. |
| `AGENT_RUNNER_ROUND_NUM` | Current round number as string (matches `round_num` field in `events-*.jsonl`). |
| `AGENT_RUNNER_PHASE` | Current phase name from rotation, or `""` (empty string) when no `[phases]` section is configured. |
| `AGENT_RUNNER_FRESH_EYES` | `"1"` on a fresh-eyes round (`round_num` a positive multiple of `[runtime] fresh_eyes_every_n`), `"0"` otherwise. Always defined. |

Example (bash):

```bash
echo "starting R$AGENT_RUNNER_ROUND_NUM phase=$AGENT_RUNNER_PHASE"
echo "done: round complete" > "$AGENT_RUNNER_LOG_DIR/.agent-done"
```

These contracts are stable; agents in any language / framework can rely
on them.

## Built-in post_round_hooks

agent-runner ships 5 built-in `post_round_hooks` plugins registered
automatically via `agent_runner.plugins`: `claude` (below),
`gemini` (0.1.24+, parallel for gemini CLI), `codewhale` (0.1.41+, parallel
for codewhale CLI), `kimi` (parallel for Kimi Code CLI), and `pi` (parallel
for Pi Coding Agent).

### `claude` (0.1.23+)

**Entry-point name:** `claude` (group `agent_runner.plugins`)
**Module:** `agent_runner.builtin_plugins.claude_rate_limit`

The plugin's identifier has moved as its scope grew: `claude_rate_limit_detector`
(single-purpose rate-limit detection) → `claude_error_detector` (0.1.23, generalized
to multi-classification) → `claude_rate_limit` (0.3.0 `PluginManifest.name`) →
`claude` (0.3.9, renamed to match the agent binary basename so its
`cooperative_stop`/`sigterm_grace_s` join onto the running agent — the module
file stays `claude_rate_limit.py`). Operators still using
`[plugins] disable = ["claude_rate_limit"]` (or the older
`["claude_error_detector"]` / `["claude_rate_limit_detector"]`) must switch to
`["claude"]` (`agent-runner migrate` flags this as a manual rename).

After each round, scans the last 200 JSON lines of the round's log (non-JSON
stderr chatter is filtered out before windowing) for transient errors and
usage data:

- A `rate_limit_event` message with `status: "rejected"` and
  `rateLimitType: "five_hour"` (account 5h quota), or
- A result with `is_error: true` and `api_error_status` in
  {429, 500, 502, 503, 504, 529, 408}.

When a transient error is detected, emits a `transient_error_detected`
event with `classification` ∈ {`rate_limit_account`, `rate_limit_model`,
`api_transient_5xx`, `api_timeout`}, plus `agent`, `reset_at_epoch`,
`round_num`, `raw` (≤200 chars), and `phase` (0.2.10 — the rotation phase the
failing round ran under, `""` when the config has no `[phases]`).

Per round (regardless of error state), also emits `agent_usage_recorded`
with token/cost/duration data extracted from the claude result event. The
payload fields are the keyword arguments of `emit_agent_usage_recorded`:

| Field | Type | Notes |
|---|---|---|
| `agent` | `str` | CLI name (`claude`, `gemini`, …) |
| `model` | `str` | model id used for the round |
| `round_num` | `int` | 1-based round number |
| `input_tokens` | `int` | net non-cached input |
| `output_tokens` | `int` | generated tokens |
| `cached_tokens` | `int` | cache-read input tokens |
| `cost_usd` | `float \| None` | round cost, or `None` when the CLI omits it |
| `duration_ms` | `int` | round wall time |
| `models_breakdown` | `dict \| None` | per-model split on multi-model rounds |
| `cache_creation_tokens` | `int` | cache-write tokens (claude only) |
| `tool_call_count` | `int` | tool invocations in the round |
| `phase` | `str` | phase label, empty when unphased |
| `success` | `bool` | supervisor clean-exit predicate |

gemini omits the cache/cost fields: `cost_usd` is `None` and
`cache_creation_tokens` is `0`. The supervisor reads `transient_error_detected`
on the next dispatch cycle and applies the configured `transient_error_action`.

No configuration required to enable the detector; it activates for any
project using claude as the agent CLI.

Non-claude agents: the detector returns early when `ctx.agent_binary != "claude"`.
Third-party plugin authors may declare an equivalent
`PluginManifest(name="my_plugin", post_round_hooks=(...))`
to ship the same event families for other agent CLIs — the bundled `gemini`
and `codewhale` plugins are working references.

### `codewhale` (0.1.41+)

**Entry-point name:** `codewhale` (group `agent_runner.plugins`)
**Module:** `agent_runner.builtin_plugins.codewhale`

Parallel to `claude` for the codewhale CLI. Returns early when
`ctx.agent_binary != "codewhale"`, so it costs nothing on other projects.
Scans the round's JSONL log tail for transient errors and emits
`transient_error_detected` with the same 4-bucket `classification` contract.

Disable with `[plugins] disable = ["codewhale"]`.

### `kimi`

**Entry-point name:** `kimi` (group `agent_runner.plugins`)
**Module:** `agent_runner.builtin_plugins.kimi`

Parallel to `claude` for the Kimi Code CLI. Returns early when
`ctx.agent_binary != "kimi"`, so it costs nothing on other projects. Requires
the preset's `--output-format stream-json`.

Kimi retries provider failures internally (up to 10 attempts with growing
delays), emitting a `{"role":"meta","type":"turn.step.retrying",...}` record
carrying `status_code` for each attempt. The detector reads the last such
record **only on a round that failed or timed out** — a round that still
succeeded absorbed the blip, and backing off after it would be a false alarm —
and emits `transient_error_detected` with the same 4-bucket `classification`
contract ({429 → `rate_limit_model`, 5xx → `api_transient_5xx`, 408 →
`api_timeout`}). Without it, a rate-limited kimi round restarts straight into
the next round with no back-off.

No `agent_usage_recorded`: the CLI's stream-json output carries no token
counters (verified against Kimi Code 0.29.1), and emitting zeros would read as
a round that cost nothing. Errors kimi does not retry — auth, unknown model —
never produce a retry record; they arrive as plain text on stderr and are
matched by the monitor's `oauth_fail` detector instead.

Disable with `[plugins] disable = ["kimi"]`.

### `pi`

**Entry-point name:** `pi` (group `agent_runner.plugins`)
**Module:** `agent_runner.builtin_plugins.pi`

Parallel to `claude` for the Pi Coding Agent. Returns early when
`ctx.agent_binary != "pi"`, so it costs nothing on other projects. Requires the
preset's `--mode json`.

Emits `agent_usage_recorded` on every round that reached the model. pi's usage
is **per-message, not cumulative** (verified against pi 0.80.10): each assistant
message carries `usage` with `input` (net of cache), `output` (reasoning
included), `cacheRead`, `cacheWrite` and a `cost` sub-object, so the round total
is the sum over the round's assistant messages — read from the `agent_end`
records, each of which lists exactly the messages its agent run produced. Rounds
that never reached the model report all-zero usage and emit nothing rather than
a record that reads as a round costing nothing. `cost_usd` is `usage.cost.total`
when the model catalog carries pricing, else null. `message_update` deltas are
never parsed: each repeats the full message state with zeroed usage and a stale
`stopReason`.

Emits `transient_error_detected` with the same 4-bucket `classification`
contract, keyed on the failing message's `errorMessage` ({429 →
`rate_limit_model`, 5xx → `api_transient_5xx`, 408 or `"Request timed out."` →
`api_timeout`}). **pi exits 0 on provider failure** — an invalid key, and three
exhausted retries against 429/503, all exited 0 with an empty stderr — so the
failure signal is the final assistant message's `stopReason == "error"` rather
than the exit code. pi self-retries up to 3 times; a blip it recovered from
leaves the final state clean and emits nothing. Auth (401) and unknown model
(404) map to no bucket: they are permanent until an operator fixes config, so
backing off would only stall.

A 401 instead emits `agent_auth_error_detected` (`round_num`, `agent`, `raw`
≤200 chars, redacted) — the third member of the plugin event family, alongside
`transient_error_detected` and `agent_usage_recorded`. It is emitted only when
the CLI's own structured output names the failure, which makes it certain
evidence: the monitor's `oauth_fail` detector counts a round carrying it
directly, without the nonzero-exit gate its text heuristic needs. That gate is
why pi's auth loops were previously invisible — pi exits 0. 403 would qualify
on the same reasoning but has not been observed from pi, so it is not parsed.

Disable with `[plugins] disable = ["pi"]`.

## Worked example: a post_round_hook that parses the round log

The common project-specific pattern is a `PostRoundHook` that reads the merged
round log after each round and records or emits something. The whole contract
is three lines — a class with a unique `name` and an `after_round` method:

```python
class RoundLogCounter:
    name = "example_round_log_counter"

    def after_round(self, ctx, result):  # ctx.agent_log_path is the round log
        ...
```

Declare it on your `PLUGIN = PluginManifest(name="my_plugin", post_round_hooks=(RoundLogCounter(),))`.
A runnable, tested reference — the minimal plugin plus a test asserting
`after_round` fires with a real `HookContext` — lives in
`tests/unit/test_example_plugin.py`; copy from there rather than from a
snippet that never runs.

`ctx.agent_log_path` is the round's **merged stdout+stderr** (auth/network
errors on stderr stay parseable); parse it as JSONL that may contain non-JSON
lines. Guard for `None` (unset on manually-constructed `HookContext` in tests),
and do not recompute the path from `ctx.log_dir` + round number — the naming
convention under that directory is not a stable contract.

The same `name` + `after_round` shape covers other signals: count git commits
per round, compare recent vs older round duration, or write to your own log —
`events.emit()` only accepts built-in kinds, so a plugin's own telemetry goes
through its own logging, not the supervisor's event stream. Project-specific
semantics live in the plugin — agent-runner core stays agent-agnostic.

## Dirty-tree resolution (core, not a plugin extension point)

After a clean-exit round leaves the working tree dirty, core resolves it per
`[vcs] dirty_action` (`stash` / `ignore` / `auto_commit` — see
`docs/configuration.md`) and records the result as
`RoundResult.dirty_outcome: DirtyOutcome | None`. `PostRoundHook` authors can
read it:

```python
from agent_runner.api_types import DirtyOutcome


def after_round(self, ctx, result):
    if result.dirty_outcome and result.dirty_outcome.kind == "committed":
        # Core auto-committed the round's dirty tree. result.dirty_outcome.ref is the SHA.
        ...
```

`DirtyOutcome.kind` is `"ignored"`, `"stashed"`, or `"committed"`; `ref` is the
stash/commit SHA (`None` for `"ignored"`). There is no override hook — dirty-tree
policy dispatches purely on `dirty_action`, so a project that wants different
behavior changes that config value rather than shipping a plugin.

## Plugin tests + consumer pytest collision

If your plugin ships its own `tests/` and the consumer project has one too,
pytest's collection walk can find both and fail with `ModuleNotFoundError`
when the same package name lives in two locations. Scope collection to your
plugin's tests in its `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["my_agent_plugin/tests"]
```
