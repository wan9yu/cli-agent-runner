# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
