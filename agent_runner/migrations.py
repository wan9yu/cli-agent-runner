"""Executable config migrations. A registry of transforms; each detects a
removed/renamed key in the parsed TOML and either renames it on the raw file
text (auto) or reports a manual instruction. Rewriting is targeted regex on the
raw text so comments and formatting are preserved — no TOML writer dependency."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    detect: Callable[[dict], bool]  # old key present in the parsed TOML?
    apply: Callable[[str], str] | None  # raw-text rewrite; None = manual-only
    describe: str  # report line (auto) or instruction (manual)


@dataclass(frozen=True)
class MigrationResult:
    new_text: str
    applied: list[str]
    manual: list[str]


def _rename_key(old: str, new: str) -> Callable[[str], str]:
    """Rename a bare TOML assignment `old = ...` to `new = ...`, preserving indent,
    spacing, value, and inline comment. Anchored to line-start assignments, so a
    commented-out `# old = ...` or `old` inside a value/string is never matched."""
    pat = re.compile(rf"(?m)^(?P<indent>[ \t]*){re.escape(old)}(?P<sp>[ \t]*=)")
    return lambda text: pat.sub(rf"\g<indent>{new}\g<sp>", text)


MIGRATIONS: list[Migration] = [
    Migration(
        detect=lambda p: "rate_limit_action" in p.get("runtime", {}),
        apply=_rename_key("rate_limit_action", "transient_error_action"),
        describe="runtime.rate_limit_action → runtime.transient_error_action",
    ),
    Migration(
        detect=lambda p: "orphan_action" in p.get("vcs", {}),
        apply=_rename_key("orphan_action", "dirty_action"),
        describe="vcs.orphan_action → vcs.dirty_action",
    ),
    Migration(
        detect=lambda p: "round_timeout_per_phase" in p.get("runtime", {}),
        apply=None,
        describe=(
            "runtime.round_timeout_per_phase (removed 0.1.16) must be moved "
            "manually to [phases.<name>] round_timeout_s"
        ),
    ),
]


def run_migrations(text: str, parsed: dict) -> MigrationResult:
    new_text = text
    applied: list[str] = []
    manual: list[str] = []
    for m in MIGRATIONS:
        if not m.detect(parsed):
            continue
        if m.apply is None:
            manual.append(m.describe)
            continue
        rewritten = m.apply(new_text)
        if rewritten == new_text:
            # Detected but the line-anchored rename matched nothing (e.g. a
            # dotted-key `runtime.rate_limit_action = ...` at top level) —
            # route to manual so we never report "applied" on an unchanged file.
            manual.append(m.describe)
            continue
        try:
            tomllib.loads(rewritten)
        except tomllib.TOMLDecodeError:
            # The rename would collide with a target key that is already present
            # (e.g. both `orphan_action` and `dirty_action` set), producing a
            # duplicate assignment — invalid TOML. Never adopt it; hand the user
            # a manual instruction instead so `migrate`/`upgrade` don't corrupt
            # the file and then traceback on the next load.
            manual.append(
                m.describe + " (target key already present — remove the deprecated key manually)"
            )
            continue
        new_text = rewritten
        applied.append(m.describe)
    return MigrationResult(new_text=new_text, applied=applied, manual=manual)
