# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.10] - 2026-05-XX

### Acknowledgements

Thanks to the Argus Gateway team for the Phase 4 second-pass production feedback that drove every change in this release. Six audit memos across 50 minutes of validated runtime surfaced four specific gaps; this release closes them.

### Added

- New built-in event kind `monitor_started`, emitted once at `monitor_loop()` entry. Records `host`, `interval_s`, `log_dir`, `mode="anomaly-only"`. Lets programmatic consumers verify supervision is up — monitor is otherwise silent under healthy operation by design.
- New exception `agent_runner.monitor.MonitorRemoteError`, raised when ssh to a `--host` target fails at protocol level (rc=255: connection refused, key reject, etc.). Previously such failures were silently swallowed.

### Changed

- `peek` schema version bumped `1.5` → `1.6` (additive: new event kind in `plugins.event_kinds`). Existing consumers unaffected.
- `detect_hung` now accepts `round_timeout_per_phase: dict[str, int] | None` and consults the per-phase timeout for each open round (falls back to `round_timeout_s` if phase missing or no per-phase override exists).
- `agent-runner stop` prints two stderr lines (`stopping service...` / `stopped (Xs)`) for ops feedback. Json mode (if applicable) remains silent.

### Fixed

- `agent-runner monitor --host <alias>` no longer silently no-ops when ssh fails at protocol level. Errors print to stderr with the underlying ssh diagnostic and exit code 1.

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

[Unreleased]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.10...HEAD
[0.1.10]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.9
[0.1.8]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.8
[0.1.7]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.7
[0.1.6]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.6
[0.1.5]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.5
[0.1.4]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.4
