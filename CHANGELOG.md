# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/wan9yu/cli-agent-runner/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.1
[0.1.0]: https://github.com/wan9yu/cli-agent-runner/releases/tag/v0.1.0
