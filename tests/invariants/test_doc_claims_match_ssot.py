"""Invariant: documented counts + config value-sets match their code SSOT.

Turns the recurring "doc says N but code says M" drift (caught repeatedly by
periodic audits) into a CI failure at the introducing commit. Curated registry:
when you reword a guarded claim, update the registry in the same change.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runner import defenses
from agent_runner.builtin_plugins._constants import _5XX_STATUSES
from agent_runner.cli import _build_parser
from agent_runner.config import _VALID_DIRTY_ACTIONS, _VALID_INJECTION_MODES, load_config
from agent_runner.monitor import KNOWN_ALERT_KINDS
from tests._test_helpers import make_toml

REPO = Path(__file__).resolve().parents[2]


def _verb_count() -> int:
    p = _build_parser()
    subs = [a for a in p._subparsers._group_actions if hasattr(a, "choices")]
    return len({c for a in subs for c in a.choices})


def test_doc_counts_match_ssot(tmp_path) -> None:
    cfg = load_config(make_toml(tmp_path))
    detectors = len(KNOWN_ALERT_KINDS)
    defs = len(defenses.catalog(cfg))
    verbs = _verb_count()

    # (file, regex with ONE int capture group, expected value)
    registry = [
        ("README.md", r"(\d+) detectors\b", detectors),
        ("README.md", r"(\d+) named defenses", defs),
        ("README.md", r"## (\d+) verbs", verbs),
        ("docs/architecture.md", r"Monitor: (\d+) detectors", detectors),
        ("docs/architecture.md", r"returns (\d+) structured", defs),
        ("docs/architecture.md", r"（(\d+) 条）", defs),
        ("docs/commands.md", r"Runs the (\d+) detectors", detectors),
        ("docs/commands.md", r"(\d+) 个动词", verbs),
    ]

    failures: list[str] = []
    for fname, pattern, expected in registry:
        text = (REPO / fname).read_text(encoding="utf-8")
        found = re.findall(pattern, text)
        if not found:
            failures.append(
                f"{fname}: pattern {pattern!r} matched nothing (reworded? update registry)"
            )
            continue
        for m in found:
            if int(m) != expected:
                failures.append(f"{fname}: claim '{m}' for {pattern!r} should be {expected}")
    assert not failures, "doc count drift:\n" + "\n".join(failures)


def _backtick_quoted_tokens(line: str) -> set[str]:
    return set(re.findall(r"`\"?(\w+)\"?`", line))


def test_doc_value_sets_match_ssot() -> None:
    failures: list[str] = []

    # dirty_action: configuration.md line "... one of `"stash"`, `"ignore"`, `"auto_commit"`"
    cfg_text = (REPO / "docs/configuration.md").read_text(encoding="utf-8")
    dirty_line = next((ln for ln in cfg_text.splitlines() if "one of" in ln and "stash" in ln), "")
    dirty_doc = _backtick_quoted_tokens(dirty_line)
    if dirty_doc != set(_VALID_DIRTY_ACTIONS):
        failures.append(f"dirty_action doc {dirty_doc} != SSOT {set(_VALID_DIRTY_ACTIONS)}")

    # context_injection_mode: bullets `- `prepend``, `- `file``, `- `none`` in its section
    mode_section = cfg_text.split("`prompt.context_injection_mode`", 1)[-1]
    mode_doc = set(re.findall(r"^- `(prepend|file|none)`", mode_section, re.MULTILINE))
    if mode_doc != set(_VALID_INJECTION_MODES):
        failures.append(
            f"context_injection_mode doc {mode_doc} != SSOT {set(_VALID_INJECTION_MODES)}"
        )

    # transient classification buckets: plugins.md "classification ∈ {`a`, `b`, `c`, `d`}"
    plug_text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    m = re.search(r"classification.*?∈\s*\{([^}]*)\}", plug_text, re.DOTALL)
    cls_doc = set(re.findall(r"`(\w+)`", m.group(1))) if m else set()
    cls_ssot = _classification_ssot()
    if cls_doc != cls_ssot:
        failures.append(f"classification doc {cls_doc} != SSOT {cls_ssot}")

    # 5xx transient statuses: runbook "server outage (500/502/...)" + plugins.md
    # "api_error_status in {429, ..., 408}" (the 5xx set plus 429 and 408).
    rb_text = (REPO / "docs/runbook.md").read_text(encoding="utf-8")
    rb = re.search(r"`api_transient_5xx` — server outage \(([\d/]+)\)", rb_text)
    rb_doc = {int(s) for s in rb.group(1).split("/")} if rb else set()
    if rb_doc != set(_5XX_STATUSES):
        failures.append(f"runbook 5xx doc {rb_doc} != SSOT {set(_5XX_STATUSES)}")

    p5 = re.search(r"`api_error_status` in\s*\{([\d,\s]+)\}", plug_text)
    p5_doc = {int(s) for s in p5.group(1).split(",")} if p5 else set()
    if p5_doc != set(_5XX_STATUSES) | {429, 408}:
        failures.append(f"plugins.md status doc {p5_doc} != SSOT {set(_5XX_STATUSES) | {429, 408}}")

    # --preset choices: commands.md "--preset {a,b,c}" must equal the derived SSOT.
    # init_cmd derives choices from presets/*.toml; the hand-written doc list must track it.
    from agent_runner.cli.init_cmd import _preset_names

    preset_ssot = set(_preset_names())
    cmds_text = (REPO / "docs/commands.md").read_text(encoding="utf-8")
    pm = re.search(r"--preset \{([^}]+)\}", cmds_text)
    preset_doc = set(pm.group(1).split(",")) if pm else set()
    if preset_doc != preset_ssot:
        failures.append(f"--preset doc {preset_doc} != SSOT {preset_ssot}")

    assert not failures, "doc value-set drift:\n" + "\n".join(failures)


def _classification_ssot() -> set[str]:
    # Mirror tests/invariants/test_classification_ssot.py's source of truth.
    from agent_runner.builtin_plugins._constants import _BACK_OFF_DEFAULTS

    return set(_BACK_OFF_DEFAULTS) | {"rate_limit_account"}
