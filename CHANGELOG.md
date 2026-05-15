# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.19] - 2026-05-15

- `agent-runner install` derives `ExecStart` via `shutil.which`; fixes
  `pip install --user` on Debian/Pi (script lives at `~/.local/bin/`).
- `agent-runner install` fail-fasts when user systemd is unavailable
  instead of silently reporting success. Prints remediation hint.
- `agent-runner install --system` shipped: writes `/etc/systemd/system/`
  with `User=$SUDO_USER`, enables (no auto-start). For headless distros.

See `docs/migrations/0.1.19.md`.

## [0.1.18] - 2026-05-15

### ⚠️ Breaking

- `vcs.orphan_action` removed (deprecated in 0.1.17). TOML using the
  old key raises `ValueError` with migration hint. Use `vcs.dirty_action`.
  See `docs/migrations/0.1.17.md`.

## [0.1.17] - 2026-05-15

- `vcs.dirty_action` replaces `vcs.orphan_action`; supports `stash` /
  `ignore` / `auto_commit`. Old name kept as deprecated alias (removed
  in 0.1.18). New `dirty_commit_failed` event for `auto_commit` failures.
- TOML relative paths now resolve against `runtime.work_dir` at load
  (`log_dir`, `narrative_file`, `prompt.file`, `prompt.files[*]`,
  per-phase prompt files).
- New round subprocess env vars `AGENT_RUNNER_ROUND_NUM` and
  `AGENT_RUNNER_PHASE`.

See `docs/migrations/0.1.17.md`.

## [0.1.16] - 2026-05-14

### ⚠️ Breaking changes

- **`runtime.round_timeout_per_phase` dict syntax removed**. Use `[phases.<name>] round_timeout_s = X` sub-table instead. Migration recipe in `docs/migrations/0.1.16.md`. Rationale: the dict syntax scaled poorly (N² as more per-phase fields would land); the sub-table is a one-time generalization that accommodates any per-phase field including the new `prompt.files`.
- **`Config.phases` type changed** from `list[str] | None` to `PhasesConfig` dataclass with `.list: list[str] | None` and `.overrides: dict[str, PhaseOverride]`. Code reading `cfg.phases` as a list directly must update to `cfg.phases.list`. Internal callers and plugins are advised to check this access pattern.

### Added

- **`[phases.<name>]` sub-table syntax** — per-phase override for `round_timeout_s`, `disable_pre_round_hooks`, `prompt.files`. Phase name must appear in `phases.list` (typo catcher); unknown fields rejected at config load.
- **`prompt.files = ["a.md", "b.md"]`** — multi-file prompt concat. Default separator `"\n\n"` (markdown-safe); customizable via `prompt.concat_separator`. Missing first file aborts; missing nth file warns + skips (supports optional preamble pattern).
- **`prompt.strip_yaml_frontmatter: bool`** — new config field, default `true`. The existing R721 frontmatter-strip defense (single-file path has applied this since prior releases) is now explicit and opt-out-able. Operators with non-LLM-CLI agents can set `false`.
- **Back-compat retained**: `prompt.file = "x.md"` single-file shorthand still works. Both `prompt.file` and `prompt.files` set → `ConfigError`.
- **`docs/events.md`** — schema versioning contract: event kind name is the version discriminator; payload fields are append-only.
- **New public API helpers**: `agent_runner.api.assemble_prompt(cfg, phase, context=None)` and `agent_runner.api.resolve_runtime_for_phase(cfg, phase_name)`.

### Migration notes

- Replace `runtime.round_timeout_per_phase = { dev = 3600, qa = 900 }` with `[phases.dev] round_timeout_s = 3600` + `[phases.qa] round_timeout_s = 900` sub-tables. See `docs/migrations/0.1.16.md` for full recipe.
- Code reading `cfg.phases` (e.g. iterating phase names) must update to `cfg.phases.list`.
- `prompt.file = "x.md"` continues to work unchanged; no migration required unless adopting multi-file concat.

### Acknowledgements

Argus Gateway's post-Q1-audit feedback (2026-05-14) surfaced the per-phase sub-table need (S3) and the multi-file prompt pattern (S1). Their R721 frontmatter-strip lesson informed making the (already-shipping) strip behavior an explicit opt-out config flag rather than hardcoded.

## [0.1.15] - 2026-05-14

### Acknowledgements

Designed in a 2026-05-14 conversation about agents that should know when their
own work is done (research / bug-fix / refactor projects with natural
completion criteria), with transparent operator visibility via browser. Two
independent components bundled into one release because both serve the same
"agent lifecycle transparency" theme.

### Added

- **Agent self-termination via `log_dir/.agent-done` sentinel**: the agent writes this file (with optional reason text, capped 200 chars in the event payload) to signal "research wrapped up". `agent-runner serve` detects the sentinel between rounds, emits `agent_self_terminated` event, and exits 0 cleanly.
- **New env var `AGENT_RUNNER_LOG_DIR`** injected into round subprocesses. Agents construct the sentinel path from it language-agnostically (bash: `echo done > "$AGENT_RUNNER_LOG_DIR/.agent-done"`).
- **New built-in event kind `agent_self_terminated`**: payload `{reason: str}`, capped 200 chars.
- **Per-round stdout/stderr capture**: round subprocess output now written to `log_dir/round-<N>.log`, with `log_dir/round-current.log` symlink atomically relinked at each round start. Retention configurable via `runtime.round_log_retention` (default 100).
- **New CLI mode `agent-runner monitor --mode http --port 8765 --config X.toml`**: browser-friendly progress page on `http://127.0.0.1:8765/`. 5-section view (round state, narrative, recent events, round log tail, self-termination flag), 5-second meta-refresh, `/api/state` JSON endpoint. Local-only (like narrate/events), zero new dependencies (stdlib `http.server`).
- **New config fields** `runtime.round_log_retention` (int, default 100) and `runtime.narrative_file` (path, default `log_dir/narrative.md`).
- **New public API helpers** `agent_runner.api.read_round_num()` and `agent_runner.api.check_self_terminated_sentinel()`.

### Migration notes

- Fully additive. No schema changes. No breaking API changes.
- **Behavior change for `agent-runner serve` under systemd**: round subprocess stdout/stderr now goes to `log_dir/round-<N>.log` instead of supervisor stdout. journalctl will no longer show per-round agent output — supervisor lifecycle messages remain. Use `tail -F log_dir/round-current.log` for live raw view, or `agent-runner monitor --mode http` for browser view.
- Agents that want to support self-termination opt in by writing the sentinel; existing agents are unaffected.

## [0.1.14] - 2026-05-14

### Acknowledgements

Two of six nice-to-have items surfaced in Argus Gateway's v0.1.12
production-evaluation report (2026-05-14). An earlier scope also included
per-phase runtime override; spec review caught that `runtime.round_timeout_per_phase`
already covers Argus's stated need, so that component was deferred to a future
release where a second per-phase field surfaces. Other items in the report
(detector helper, hot-reload, replay) remain intentionally out of scope.

### Added

- New plugin extension point `agent_runner.serve_startup_hooks` (entry_points group). Hooks implement `ServeStartupHook` protocol — `name: str` + `__call__(cfg: Config) -> None`. Fires once per `agent-runner serve` invocation, after config load, before the supervisor loop. Useful for seeding state that subsequent rounds depend on (e.g. default prompt file).
- New built-in event kind `serve_startup_hook_failed` — emitted (best-effort) when a serve-startup hook raises. Payload: `hook` (name), `exc_type`, `exc_msg` (capped 200 chars). `agent-runner serve` aborts with exit code 1.
- New CLI mode `agent-runner monitor --mode events` — JSONL event stream to stdout, one event per line, line-buffered for pipe-friendliness. Subscription starts at "now" (no historical replay). Follows daily file rotation transparently. Local-only (like `narrate` mode).

### Migration notes

- Fully additive release. No schema changes. No breaking API changes.
- Plugin authors: registering a serve-startup hook follows the same pattern as `pre_round_hooks` / `post_round_hooks` — declare an entry point under `agent_runner.serve_startup_hooks`, register via `register_serve_startup_hook()` at module import.

## [0.1.13] - 2026-05-14

### Acknowledgements

Thanks to the Argus Gateway team — this release answers their imminent
production-deployment requirement for upgrade-without-disruption. After
confirming round duration (10-40 min) fits within graceful-stop tolerance,
this release scopes to round-boundary upgrade UX (Level 1). Mid-round
adoption (Level 2) was considered and explicitly deferred — the exit-code
recovery problem makes it materially harder, and graceful stop covers the
real production need.

### Added

- New CLI subcommand `agent-runner upgrade [--target VERSION]` — single-command upgrade flow: graceful stop → `pip install --upgrade cli-agent-runner[==<target>]` → smoke check the new binary (`--version` + `peek --json`) → start. `--target` defaults to PyPI latest; pass an explicit version to roll back: `agent-runner upgrade --target 0.1.12`.
- New built-in event kind `service_upgraded` — emitted on successful upgrade. Payload: `from_version`, `to_version`, `duration_s`.
- New built-in event kind `service_upgrade_rolled_back` — emitted when smoke check fails on the new version and the supervisor auto-rollbacks via `pip install --force-reinstall cli-agent-runner==<from_version>`. Payload: `attempted_version`, `restored_version`, `failure_reason` (first 200 chars), `duration_s`. Exit code 1.
- New built-in event kind `service_upgrade_rollback_failed` — emitted in the worst case where rollback itself fails (rare). Service is left stopped; manual intervention required. Payload: `attempted_version`, `restore_target_version`, `failure_reason`. Exit code 2.

### Migration notes

- New CLI subcommand. No behavior change for existing CLI verbs.
- `pip` is assumed to be on `PATH` for `agent-runner upgrade`. If absent or broken, the install step fails with a clear error and the service remains stopped — operator can run `agent-runner start` to resume the previous version.
- Smoke step uses `subprocess.run([sys.executable, "-m", "agent_runner.cli", ...])` — it spawns a fresh Python process so it imports the freshly-installed code, not the old code already loaded in the upgrade command's own process. This is correctness-critical.

## [0.1.12] - 2026-05-14

### Acknowledgements

Thanks to the Argus Gateway team for the deep v0.1.10 audit-session feedback
(6 items, 3-round real-run testing on ARMv8 Pi). This release reframes those
items into a coherent "Plugin & Operator Transparency" theme across three
layers: transparency (see what plugins do), operator override (escape hatches
for audit/debug), and diagnostic quality (errors point at the next debug step).

### Added

- New CLI flag `agent-runner round --phase NAME` — explicit phase override (audit, debug, multi-script orchestration). Does NOT mutate the internal rotation counter; subsequent default rounds resume rotation.
- New TOML field `[plugins] disable = ["entry_point_name", ...]` — selectively disable plugin entry_points without uninstalling the package. Surfaced in `peek --json` under `plugins.disabled`.
- New TOML field `[runtime] disable_pre_round_hooks = true` — temporary all-PreRoundHooks-off mode (audit/debug). Complements `[plugins] disable`'s per-name granularity.
- New built-in event kind `prompt_overwritten` — emitted after each PreRoundHook that mutates `cfg.prompt.file` content. Payload: `hook` (name), `prompt_path`, `old_hash`, `new_hash` (sha256-prefixed). Hash-per-hook approach gives precise attribution; multiple mutating hooks per round → multiple events (chained).
- New CLI flag `agent-runner monitor --mode {anomaly,narrate}` — `narrate` streams `events.jsonl` with one-line-per-event format (debug/audit visibility). Default `anomaly` preserves existing behavior. Local-only; `--host` + `narrate` rejected.
- LockHeldError now carries holder info — PID, age (seconds), cmdline (first 80 chars) via sidecar file pattern. Diagnostic-only; tolerates missing/stale sidecar gracefully.

### Changed

- `peek --json` schema bumped `1.7` → `1.8` (additive: new `plugins.disabled` sub-key).
- `Config.plugins` type refactored from free-form `dict[str, Any] | None` to typed `PluginsConfig` dataclass. `cfg.plugins.disable: list[str]` is first-class; unknown TOML keys land in `cfg.plugins.raw: dict[str, Any]` for forward-compat with plugin-author-defined `[plugins.*]` sub-keys.

### Migration notes

- `Config.plugins` type change is breaking for any caller reading the field as a dict (`cfg.plugins.get("foo")`). Plugin authors using `[plugins.argus_*]`-style keys: read them from `cfg.plugins.raw.get("argus_*")` instead.
- `LockHeldError` message format changed (now includes holder info: `"another agent-runner is holding /path (held by PID N, age Ns, cmd: ...)"` or stale/missing variants). Operators grepping the exact format string need to update.
- For Argus's P5 confusion: see new `docs/architecture.md` section "Plugin injection: two paths" — `inject_context` and `disable_pre_round_hooks` are INDEPENDENT flags. Setting one does not affect the other.
- **Known limitation**: `[plugins] disable` removes named plugins from the hook / context-enricher / detector / event-kind registries, but does NOT remove a disabled plugin's owned VCS paths (the `register_plugin_owned_paths` registry has no name attribution today). Mostly inert. If this becomes a real issue, file a GitHub issue.

## [0.1.11] - 2026-05-13

### Acknowledgements

Thanks again to the Argus Gateway team — this release closes the
network-resilience gap that 0.1.10's `MonitorRemoteError` propagation
exposed, plus adds per-occurrence agent network blip observability requested
during 0.1.10 handover review.

### Added

- New CLI flag `agent-runner --version` prints `agent-runner <version>` (sourced from hatch-vcs metadata).
- New built-in event kind `monitor_remote_blip` — emitted per `_poll_once` failure in `monitor --host` while still within `remote_failure_tolerance_s`. Payload: `host`, `error`, `attempt`, `elapsed_s`, `cap_s`, `interval_s`, `next_sleep_s`.
- New built-in event kind `monitor_remote_giveup` — emitted once when retries exceed the tolerance window, just before the error propagates. Payload: `host`, `total_attempts`, `total_elapsed_s`, `cap_s`, `final_error`. Distinguishes "blip storm recovered" from "blip storm won" in postmortem.
- New built-in event kind `agent_network_blip` — emitted at end of each round whose log matches network-error patterns. Payload: `round_num`, `phase`, `matched` (regex match substring), `round_duration_s`, `exit_code`, `timed_out`. Independent of the rate-based `detect_network_fail` alert.
- New config field `[monitor] remote_failure_tolerance_s` (default 90s, range [0, 3600]). 0 preserves 0.1.10's immediate-propagate behavior.
- `ProjectState.recent_blips: list[dict[str, Any]]` field (last 5 `agent_network_blip` events).

### Changed

- `peek` schema version bumped `1.6` → `1.7` (additive: new event kinds + `recent_blips` field).
- `monitor --host`'s steady-state polling now tolerates transient ssh failures up to 90 seconds with exponential backoff (1s → 2s → 4s → 8s → 16s → 30s cap). After the tolerance window, the error propagates as before (CLI exits 1; systemd restarts).

### Postmortem trail

For network-related failures, the events index points at the diagnostic body:
- `monitor_remote_giveup` event → look for `monitor_started` events around it to see restart cadence
- `agent_network_blip` event → read `{log_dir}/rounds/R{round_num}-*.log` for the full agent output

## [0.1.10] - 2026-05-13

### Acknowledgements

Thanks to the Argus Gateway team for the Phase 4 second-pass production feedback that drove every change in this release. Six audit memos across 50 minutes of validated runtime surfaced four specific gaps; this release closes them.

### Added

- New built-in event kind `monitor_started`, emitted once at `monitor_loop()` entry. Records `host`, `interval_s`, `log_dir`, `mode="anomaly-only"`. Lets programmatic consumers verify supervision is up — monitor is otherwise silent under healthy operation by design.
- New exception `agent_runner.monitor.MonitorRemoteError`, raised when ssh to a `--host` target fails at protocol level (rc=255: connection refused, key reject, etc.). Previously such failures were silently swallowed.
- New built-in event kind `monitor_auto_stop_failed` — emitted when an auto-stop alert fires for a `--host` target but the remote ssh fails. Includes `detector`, `host`, and `error` fields.

### Changed

- `peek` schema version bumped `1.5` → `1.6` (additive: new event kind in `plugins.event_kinds`). Existing consumers unaffected.
- `detect_hung` now accepts `round_timeout_per_phase: dict[str, int] | None` and consults the per-phase timeout for each open round (falls back to `round_timeout_s` if phase missing or no per-phase override exists).
- `agent-runner stop` prints two stderr lines (`stopping service...` / `stopped (Xs)`) for ops feedback. Json mode (if applicable) remains silent.
- `agent_runner.events.emit(log_dir, kind, /, **fields)` — `log_dir` and `kind` are now positional-only so callers can use `log_dir=` as a payload field name. Non-breaking for in-repo callers (all use positional form); third-party plugins that called `events.emit(log_dir=..., kind=...)` must switch to positional form.

### Fixed

- `agent-runner monitor --host <alias>` no longer silently no-ops when ssh fails at protocol level. Errors print to stderr with the underlying ssh diagnostic and exit code 1.

### Migration notes

- **`MonitorRemoteError` propagation**: this exception now raises from `agent_runner.monitor.run_remote_command` whenever ssh exits with rc=255. The CLI `agent-runner monitor --host` catches it and exits 1 with a clear stderr message. But it also propagates from steady-state remote polls (`RemoteSource._list`) and from the auto-stop path (`on_alert` remote stop). Previously rc=255 was silently tolerated in those paths — empty list returned, no alert raised. Programmatic consumers of `monitor_loop()` that want the old transient-tolerant behavior must wrap the loop in `try: except MonitorRemoteError:`. The change is intentional (silent ssh failures were a bug, not a feature), but the behavior surface is wider than the headline Fix suggests.
- **Hardening**: ssh hosts starting with `-` are now rejected (ProxyCommand injection defense). Project names (work_dir basename) must match `[A-Za-z0-9._-]+`. Remote `auto_stop_service` failures emit a new `monitor_auto_stop_failed` event instead of crashing the monitor loop.

## [0.1.9] - 2026-05-13

### Acknowledgements

Thanks to the argus-gateway team for the dev/qa/product wall-time data
(Phase 4 feedback §3.1) that drove this API shape. Their three-role
distribution made the case for per-phase overrides concrete.

### Added

- `[runtime.round_timeout_per_phase]` TOML block — per-phase overrides for
  `round_timeout_s`. Unconfigured phases fall back to global. Keys validated
  against `[phases] list` at config-load (typo catcher); non-positive values
  rejected; bool / float values rejected (would otherwise silently coerce
  to int).
- `agent_runner.runner._round_timeout_for(cfg, phase)` helper — single
  lookup point for phase-aware timeout resolution.

### Migration

No breaking changes. Existing configs without the new block keep using a
single global timeout — identical to 0.1.8 behavior.

Plugin authors: no public API change. `RuntimeConfig` is not in the
documented plugin-author public surface.

## [0.1.8] - 2026-05-13

### Acknowledgements

Thanks to the argus-gateway team for Phase 4 dogfooding feedback that drove
every item in this release. 3 audit memos (~90KB) silently swept into an
orphan stash is a real-world failure mode; this release closes that loop.

### Added

- `agent_runner.vcs_state.register_plugin_owned_paths()` — plugins opt-out
  files/dirs from orphan-stash defense. Matching: trailing-slash prefix or
  `pathlib.PurePath.match` glob (recognizes `**` for recursive segments via
  `fnmatch` fallback on Python 3.11). Call at module import (entry_point
  side-effect).
- `agent_runner.vcs_state.plugin_owned_paths()` — snapshot accessor for peek.
- `ProjectState.recent_hook_failures: list[dict]` — last 10 `hook_failed`
  events filtered from `recent_events` for debugging hook integration.
- peek schema bumped 1.4 → 1.5. `plugins` block now includes
  `pre_round_hooks`, `post_round_hooks`, `owned_paths` lists.

### Changed

- `docs/plugins.md` register-pattern examples corrected: registration must
  happen as module-top side effect; entry_point loaders only import, they
  do not invoke. Old `_register()` wrapper pattern silently didn't fire.
- `docs/plugins.md` gained "Declaring plugin-owned paths" and "Plugin tests
  + consumer pytest collision" sections.

### Fixed

- Plugin outputs in plugin-declared paths (e.g. `proposals/`,
  `logs/plugins/my_plugin/`) no longer silently swept into orphan stashes
  by `process_orphan_wip`. Previously: 90KB Argus audit memos invisible
  after Phase 4 round; required stash archaeology to recover.

### Migration

No breaking changes. Plugin authors:

- If your plugin writes files to `work_dir` and they keep getting stashed
  between rounds, opt them out:
  ```python
  from agent_runner.vcs_state import register_plugin_owned_paths
  register_plugin_owned_paths(["your-output-dir/", "logs/your-plugin/**/*"])
  ```
- If you followed the old `_register()` pattern from docs and noticed
  registrations not firing: move the call to module top:
  ```python
  # was: def _register(): register_pre_round_hook(MyHook())
  # now: register_pre_round_hook(MyHook())  # module-top side-effect
  ```

## [0.1.7] - 2026-05-13

### Migration for existing 0.1.6 users (DOWNSTREAM CONSUMERS READ THIS)

If you maintain an `agent-runner.toml` by hand (rather than via `agent-runner init`),
you must add an `[agent.env]` block to preserve the Claude self-update suppression
that 0.1.6 injected implicitly. **Without this, mid-loop self-updates can race with
the supervisor.**

Add to your `agent-runner.toml`:

```toml
[agent.env]
DISABLE_AUTOUPDATER = "1"
CLAUDE_CODE_EFFORT_LEVEL = "xhigh"
```

Or regenerate cleanly:

```bash
agent-runner init --preset claude --force
```

Plugin authors (Argus Gateway, etc.): no public API was renamed or removed
from your import surface. The deleted symbols (`agent_runner.agent_runtime.CRITICAL_ENV_DEFAULTS`,
`agent_runner.agent_runtime.merge_critical_envs`) were internal — not part of
the documented plugin API. A new public-API contract test
(`tests/contract/test_public_api_surface.py`) locks in `api_types`, `events`,
`hooks`, `monitor`, `detector_helpers` import surfaces so future refactors
can't silently drop names you rely on.

### Added
- `agent-runner init --preset {claude,aider}` selects between bundled CLI presets
  (default: `claude`). New preset directory: `agent_runner/presets/`.
- `[agent.env]` TOML block — per-CLI env injections, replacing the hardcoded
  `CRITICAL_ENV_DEFAULTS` constant. Empty dict by default.
- `docs/recipes/aider.md` — aider integration recipe.
- `tests/contract/test_public_api_surface.py` — public-API surface snapshot for
  plugin authors.

### Changed
- Core code (`agent_runtime.py`, `config.py`, `runner.py`, `scaffold.py`,
  `defenses.py`) is now truly provider-agnostic: zero hardcoded Claude defaults.
  Claude remains the reference example throughout the docs, but its specifics
  live in `agent_runner/presets/claude.toml` (shipped as package data).
- `MonitorConfig.auth_fail_hint` default is now empty string; per-CLI hint
  comes from the preset.
- `PEEK_SCHEMA_VERSION` bumped 1.3 → 1.4. `InitResult` gains `preset: str` field.
- `agent_runner/defenses.py` `critical_envs_injection` row now reads from
  `cfg.agent.env.keys()` (not the deleted constant); state is "active" iff
  the config defines any env injections, else "off".

### Removed
- `agent_runner.agent_runtime.CRITICAL_ENV_DEFAULTS` constant.
- `agent_runner.agent_runtime.merge_critical_envs()` function.
- `agent_runner.config._DEFAULT_AUTH_HINT` constant's Claude-specific default
  string (replaced with `""`; per-CLI hint now in preset files).

## [0.1.6] - 2026-05-12

Zero-feature maintenance release — internal cleanup pass after the 0.1.x plugin
extension surface completed in 0.1.5. No runtime behavior change for users
without plugins; plugin authors written against 0.1.5 keep working unchanged.

### Changed
- Public `Alert.severity` / `Alert.auto_action` and `Detector.severity` /
  `Detector.auto_action` are now typed with `Severity = Literal["info", "warning",
  "critical"]` and `AutoAction = Literal["none", "stop_service"]` aliases
  exported from `agent_runner.api_types`. Plain-string values continue to work
- `__version__` is read from the hatch-vcs generated `_version.py` instead of a
  hardcoded constant; released packages report the actual release version
- Internal duplication reduced: shared `agent_runner._registry.ensure_unique`
  for hooks + detectors registries; `cli/common.emit()` plugin-namespace imports
  hoisted to module top; `events.parse_iso_ms` centralizes the ISO-8601 trailing-`Z`
  parsing workaround; `MonitorConfig.auto_stop_on` default now references the
  `_DEFAULT_AUTO_STOP_ON` constant
- `monitor.dual_source_silence` is now TOCTOU-safe (uses `try/except
  FileNotFoundError` instead of `exists()` + `stat()`)
- `api._poll_once` short-circuits when no plugin detectors are registered,
  skipping `assemble_project_state` (saves a file read per local poll and an
  SSH round-trip per remote poll)
- `agent_runner/__init__.py` plugin loaders share a single
  `_load_plugins_from_group` helper
- `monitor._alert` no longer asserts builtin detector names against
  `KNOWN_ALERT_KINDS` (assertion was redundant and would crash under
  `python -O`); test + docgen layers continue to validate the builtin name set

### Removed
- `agent_runner.critic` — empty Protocol stub for an unimplemented Critic
  concept that was retired during the framework-first redesign
- `[llm]` commented block from the scaffold TOML template and its corresponding
  section in `docs/configuration.md` — unused placeholder
- Legacy phase nomenclature from source docstrings, error messages, and CLI
  help text; `tests/invariants/test_phase2_*.py` files renamed to drop the
  `phase2_` prefix

### Backward compatibility
- Existing `agent-runner.toml` files continue to load (the removed `[llm]`
  block was never parsed by the supervisor)
- Plugin code written for 0.1.5 continues to work — Protocol contracts and
  `peek --json` schema 1.3 unchanged

## [0.1.5] - 2026-05-12

### Added
- `agent_runner.api_types.Detector` — public Protocol for plugin detectors
  (attributes `name`, `severity`, `auto_action`; method `detect(state) -> Alert | None`).
  `@runtime_checkable` — plugin classes structurally satisfying the shape are accepted
- `agent_runner.monitor.register_detector(detector)` and `plugin_detectors()` — public
  registration API + sorted list of currently-registered detector names
- `agent_runner.monitor.run_plugin_detectors(state)` — invokes each registered plugin
  detector with the assembled `ProjectState`; per-detector exceptions surface as
  `UserWarning` (round continues)
- `agent_runner` package now also discovers and loads entry_points in group
  `agent_runner.detectors` at first import; failures degrade to `UserWarning`
- `agent_runner.detector_helpers` module — three production-tested helpers for
  plugin detector authors:
  - `cumulative_window_check(events, *, kind, window_s, min_count)` —
    sliding-window event counter; robust against wall-clock skew at boundaries
  - `dual_source_silence(scheduler_log, round_log, threshold_s)` — both-source
    silence check; avoids false positives during long rounds when only the
    scheduler log is stale
  - `phase_filter(state, *, exclude_phases)` — skip detection during phases
    that intentionally produce no commits (e.g., retrospective rounds)
- `MonitorConfig.auto_stop_on: list[str]` — explicit allow-list of detector
  names whose `stop_service` action is honored. Defaults to
  `["oauth_fail", "disk_critical"]` (builtin pair); operators must add plugin
  detector names to opt them into auto-stop
- `monitor.on_alert` gains `allowed_stop_names: list[str] | None` keyword
  argument; backward-compatible default falls back to the legacy builtin pair
- `peek --json` schema bumped to `"1.3"`; new `plugins.detectors: list[str]`
  surfaces what's registered
- `docs/plugins.md` gains a Detector Protocol chapter + DetectorHelpers
  chapter with worked examples for each helper

### Changed
- `agent_runner.api._poll_once` now concatenates builtin detector alerts
  with plugin detector alerts (`run_plugin_detectors(state)`) before returning
- `agent_runner.api.monitor_loop` threads `cfg.monitor.auto_stop_on` into
  `monitor.on_alert` for strict gating
- `tests/invariants/test_peek_schema_version.py` tightened to require
  `schema_version >= "1.3"` and the `plugins.detectors` list shape

### Backward compatibility
- Zero plugins installed → monitor behavior identical to 0.1.4
- Existing 9 builtin detectors keep their current signatures unchanged
- Existing `agent-runner.toml` files load without modification; default
  `auto_stop_on` matches the previously-implicit behavior
- `on_alert(...)` callers without the new kwarg continue to work via the
  default `["oauth_fail", "disk_critical"]` allow-list

## [0.1.4] - 2026-05-12

### Added
- `agent_runner.hooks` module — three Protocol-typed plugin extension points:
  `PreRoundHook` (runs after lock acquire, before context write),
  `ContextEnricher` (returns a per-plugin slice stitched into `round-context.json`
  under `base_context[enricher.name]`), and `PostRoundHook` (runs after agent exit,
  before `round_end` event)
- `agent_runner.hooks.HookContext` — narrow runtime context passed to all hooks
  (`work_dir`, `log_dir`, `project`, `round_num`, `phase`, `agent_name`); does NOT
  expose the full `Config` so internal refactors remain safe
- `agent_runner.hooks.register_pre_round_hook` / `register_context_enricher` /
  `register_post_round_hook` — public registration API; rejects duplicate `name`
- `agent_runner.hooks.plugin_context_enrichers()` — sorted list of registered
  enricher names, surfaced via `peek --json`
- `agent_runner.api_types.RoundResult` — promoted from `runner.py`; superset of
  the prior internal fields plus `phase`, `started_at`, `ended_at`, `log_path` for
  stable `PostRoundHook` consumption
- `agent_runner` package now also discovers and loads three new entry_points groups
  at first import: `agent_runner.pre_round_hooks`, `agent_runner.context_enrichers`,
  `agent_runner.post_round_hooks`; plugin failures degrade to a `UserWarning`
- Built-in event kind `hook_failed` — emitted by the runner whenever a plugin hook
  raises; payload includes `{hook_name, hook_kind, error_type, error_message, traceback}`
  with traceback truncated to 2KB (head 1KB + tail 1KB joined by `[truncated]`)
- `peek --json` schema bumped to `"1.2"`; new `plugins.context_enrichers: list[str]`
- `docs/plugins.md` — new chapter with end-to-end ContextEnricher example
- New invariant `tests/invariants/test_round_result_stable.py` guards `RoundResult`
  field set + types across future minors

### Changed
- `agent_runner.runner.run_one_round` integrates hooks at three checkpoints; each
  call is wrapped in `try/except` and surfaces failures via `hook_failed`
- `tests/invariants/test_peek_schema_version.py` tightened to require
  `schema_version >= "1.2"` and the `plugins.context_enrichers` list shape

### Backward compatibility
- Zero plugins installed → runner behavior identical to 0.1.3
- `RoundResult` field set is a superset; every prior field is preserved
- Existing 0.1.x user `agent-runner.toml` files load without modification

## [0.1.3] - 2026-05-12

### Added
- `agent_runner.events.register_event_kind(name, *, source)` — public API for plugins to register custom event kinds
- `agent_runner.events.plugin_event_kinds()` — sorted list of currently-registered plugin event kind names
- `agent_runner` package now discovers and loads entry_points in group `agent_runner.event_kinds` at first import; plugin failures degrade to a `UserWarning` instead of crashing the supervisor
- `peek --json` schema bumped to `"1.1"`; new top-level `plugins.event_kinds: list[str]` surfaces what's registered
- `docs/plugins.md` — plugin authoring stub covering the entry_points convention and the event-kind example

### Changed
- `agent_runner.events.KNOWN_EVENT_KINDS` is now a read-only union view (built-in + plugin) instead of a `frozenset`; `in` and iteration semantics preserved
- `tests/invariants/test_event_kind_registry.py` rewritten to validate the built-in/plugin split and the registration conflict rules
- `tests/invariants/test_peek_schema_version.py` tightened to require `schema_version >= "1.1"` and the `plugins.event_kinds` list shape

### Backward compatibility
- Every existing `from agent_runner.events import KNOWN_EVENT_KINDS` import continues to work
- Every existing `events.emit(...)` callsite continues to work with the same kind strings
- Existing 0.1.x user `agent-runner.toml` files load without modification

## [0.1.2] - 2026-05-12

### Added
- `cfg.agent.name` — optional provider identifier; defaults to `None` (consumers may fall back to `command[0]`)
- `cfg.prompt.context_injection_mode` — `prepend` (default) / `file` / `none`; controls how round-context reaches the agent
- `cfg.monitor.auth_fail_patterns` and `cfg.monitor.auth_fail_hint` — generalize the OAuth-fail detector for any provider
- `cfg.plugins` — placeholder for 0.1.3+ plugin enable/disable; parsed-but-unused in 0.1.2
- `peek --json` now emits top-level `schema_version: "1.0"` and `plugins: {}` namespace

### Changed
- `startup_check` prompt-smoke error text reframed away from provider-specific wording
- `scaffold.py` generated TOML annotates `[agent]` block as the reference; encourages swapping for other CLIs
- Public docs (architecture, quickstart, configuration) reframed: the reference agent is one of many supported CLIs
- `monitor.detect_oauth_fail` now reads patterns + hint from `cfg.monitor` (SSOT migrated from a hardcoded module constant to `MonitorConfig`)

### Backward compatibility
- All defaults preserve the existing prepend-mode behavior
- Existing `agent-runner.toml` files continue to load without modification
- Existing tests pass without modification

## [0.1.1] — 2026-05-12

Post-release polish: PyPI install path is documented as the primary entry,
the repository was renamed for naming parity with the distribution, and the
release workflow now auto-creates GitHub Releases.

### Changed
- README + README.zh now show `pip install cli-agent-runner` as the primary
  install path; the previous `git clone` flow moved to the Development section.
- Repository renamed from `wan9yu/agent-runner` to `wan9yu/cli-agent-runner`
  for parity with the PyPI distribution name. All in-tree URL references
  updated; GitHub redirects keep old links working.

### Build & CI
- Release workflow now creates a GitHub Release after PyPI publish, attaches
  the sdist + wheel as release artifacts, and pulls release notes from the
  matching CHANGELOG section.

## [0.1.0] — 2026-05-12

Initial public release on PyPI as `cli-agent-runner`.

### Added
- Three-layer model: Round / Loop / Witness.
- 13 CLI verbs: `init`, `install`, `uninstall`, `start`, `stop`, `kill`,
  `cancel`, `restart`, `status`, `round`, `serve`, `peek`, `watch`, `monitor`.
- 11 named defenses (round timeout, process group isolation, orphan stash
  with SHA lock, set-diff classification, smoke check, flock concurrency,
  atomic state writes, event kind registry, and others).
- 9 monitor detectors (`timeout_rate`, `hung`, `orphan_chain`,
  `disk_warning`, `disk_critical`, `mem_pressure`, `smoke_fail_rate`,
  `oauth_fail`, `network_fail`); `oauth_fail` and `disk_critical` auto-stop
  the service.
- Local-only and remote (via ssh) monitor modes.
- Auto-generated docs (`./build.sh docs`) for defenses table, detectors,
  events, config schema, CLI verbs.
- Literate quickstart: `docs/quickstart.md` is executed as a pytest test.
- Python public API (`from agent_runner import api`) mirroring CLI verbs.
- Phase 3 reservation: `[llm]` config section + `agent_runner.critic`
  Protocol stubs (no implementation).

### Documentation
- English README + full Chinese `README.zh.md`.
- 5 user-facing docs: quickstart, commands, configuration, runbook, architecture.

### Build & CI
- GitHub Actions matrix: Python 3.11/3.12/3.13 × ubuntu / macos.
- Codecov-uploaded coverage from one canonical matrix cell.
- Tag-triggered release publishing to PyPI via Trusted Publishing OIDC,
  gated by a manual approval on the `pypi` GitHub environment.

[Unreleased]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.19...HEAD
[0.1.19]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.18...v0.1.19
[0.1.18]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.17...v0.1.18
[0.1.17]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.16...v0.1.17
[0.1.16]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.15...v0.1.16
[0.1.15]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.14...v0.1.15
[0.1.14]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.13...v0.1.14
[0.1.13]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.12...v0.1.13
[0.1.12]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.11...v0.1.12
[0.1.11]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.10...v0.1.11
[0.1.10]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.9
[0.1.8]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.8
[0.1.7]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.7
[0.1.6]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.6
[0.1.5]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.5
[0.1.4]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.4
