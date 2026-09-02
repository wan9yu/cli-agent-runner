"""Executable config migrations. A registry of transforms; each detects a
removed/renamed key in the parsed TOML and either renames it on the raw file
text (auto) or reports a manual instruction. Rewriting is targeted regex on the
raw text so comments and formatting are preserved — no TOML writer dependency."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_runner.config import (
    _AGENT_ALLOWED_FIELDS,
    _MONITOR_ALLOWED_FIELDS,
    _PHASE_PROMPT_ALLOWED_FIELDS,
    _PROMPT_ALLOWED_FIELDS,
    _RUNTIME_ALLOWED_FIELDS,
    _SCHEDULE_ALLOWED_FIELDS,
    _VCS_ALLOWED_FIELDS,
)

# Legacy keys already handled by their own dedicated (auto-fixable) Migration
# below — excluded from the generic "unknown key" detectors so a config using
# ONLY a legacy key isn't double-reported as both a rename AND an unknown key.
_RUNTIME_LEGACY_FIELDS = frozenset({"round_timeout_per_phase", "rate_limit_action"})
_VCS_LEGACY_FIELDS = frozenset({"orphan_action"})


@dataclass(frozen=True)
class Migration:
    detect: Callable[[dict], bool]  # old key present in the parsed TOML?
    apply: Callable[[str], str] | None  # raw-text rewrite; None = manual-only
    # Report line (auto) or instruction (manual/advisory). Usually a plain
    # string so the docs gen-block (agent_runner/_docgen.py
    # _render_migrate_transforms) can render the registry statically with no
    # parsed config in hand. A handful of unknown-key transforms instead pass
    # a `parsed -> str` callable so the manual report can name the exact
    # offending key(s); docgen calls it with `{}` and gets the same generic
    # instruction back (no keys to list yet).
    describe: str | Callable[[dict], str]
    # True for a report-only entry describing a shape that IS still valid (a
    # permanent alias, e.g. the flat per-phase override) — surfaced by
    # `migrate` as guidance but never counted as a `manual` blocker.
    # `upgrade` must not refuse to cross the version boundary over a config
    # shape that was never actually rejected.
    advisory: bool = False


@dataclass(frozen=True)
class MigrationResult:
    new_text: str
    applied: list[str]
    manual: list[str]
    advisory: list[str] = field(default_factory=list)


def _table(p: dict, name: str) -> dict:
    """Return p[name] as a dict, or {} when absent or not a table (the
    table-as-scalar footgun, e.g. `monitor = 1`) — so every OTHER detector
    that inspects the table's contents can safely `.get()`/`in`/`set()` the
    result without crashing. The scalar-table state itself is caught by its
    own dedicated Migration (see _scalar_tables below)."""
    v = p.get(name, {})
    return v if isinstance(v, dict) else {}


# Every top-level table Config parses out of the raw TOML dict.
_TOP_LEVEL_TABLES = (
    "agent",
    "runtime",
    "prompt",
    "vcs",
    "monitor",
    "phases",
    "plugins",
    "schedule",
)


def _scalar_tables(p: dict) -> list[str]:
    """Top-level tables given as a scalar (e.g. `agent = 1` instead of
    `[agent]`) — the table-as-scalar footgun 0.2.13 hard-rejects at load. No
    auto-fix is possible; real table content is needed."""
    return [t for t in _TOP_LEVEL_TABLES if t in p and not isinstance(p[t], dict)]


# Flat override fields that also live under a nested [phases.<name>.runtime]
# sub-table. The flat form is a permanent alias, so this is guidance only.
_PHASE_RUNTIME_FLAT_FIELDS = ("round_timeout_s", "disable_pre_round_hooks")


def _has_flat_phase_override(parsed: dict) -> bool:
    """True if a flat runtime override sits directly under a [phases.<name>]
    table. The phase-name sub-tables are the entries of [phases] other than the
    reserved `list`/`phase_policy` keys."""
    for name, sub in _table(parsed, "phases").items():
        if name in ("list", "phase_policy") or not isinstance(sub, dict):
            continue
        if any(fld in sub for fld in _PHASE_RUNTIME_FLAT_FIELDS):
            return True
    return False


def _agent_missing_prompt_placeholder(agent_d: object) -> bool:
    """True if this [agent]-shaped table uses argv delivery with a
    prompt_arg_template that has no {prompt} placeholder -- the prompt would
    never reach the agent. Defensive against non-dict/non-list shapes (each
    already separately detected/rejected elsewhere) and prompt_delivery
    "stdin" (a different, valid rule enforced at config load)."""
    if not isinstance(agent_d, dict):
        return False
    if agent_d.get("prompt_delivery", "argv") != "argv":
        return False
    template = agent_d.get("prompt_arg_template")
    if not isinstance(template, list):
        return False
    return not any(isinstance(a, str) and "{prompt}" in a for a in template)


def _phase_missing_prompt_placeholder(sub: dict) -> bool:
    """Mirrors config.py's per-phase carve-out: a phase whose own
    [..prompt] disables the prompt (files = []) legitimately needs no
    {prompt} token in its own [..agent] override."""
    if not _agent_missing_prompt_placeholder(sub.get("agent")):
        return False
    prompt_sub = sub.get("prompt")
    disabled = isinstance(prompt_sub, dict) and prompt_sub.get("files") == []
    return not disabled


def _phases_scalar_keys(p: dict) -> list[str]:
    """Stray scalar keys directly under [phases] other than the two reserved
    ones — the config.py footgun of a typo'd-out-of-a-sub-table field that
    used to be silently skipped."""
    return sorted(
        k
        for k, v in _table(p, "phases").items()
        if k not in ("list", "phase_policy") and not isinstance(v, dict)
    )


_TABLE_HEADER = re.compile(r"^\s*\[(?P<name>[^\]]+)\]")


def _rename_key(old: str, new: str, table: str) -> Callable[[str], str]:
    """Rename `old = ...` to `new = ...` but ONLY inside the [table] section.
    Tracks the current `[header]` while scanning line-by-line, so a same-named
    key in another table (e.g. a `[plugins.*]` sub-table) is left alone. If the
    assignment resolves to anything other than exactly one line in the target
    table, the rewrite is refused (text returned unchanged) and run_migrations
    routes it to manual."""
    assign = re.compile(rf"^(?P<indent>[ \t]*){re.escape(old)}(?P<sp>[ \t]*=)")

    def _apply(text: str) -> str:
        lines = text.splitlines(keepends=True)
        cur: str | None = None
        hits: list[int] = []
        for i, line in enumerate(lines):
            h = _TABLE_HEADER.match(line)
            if h:
                cur = h.group("name").strip()
                continue
            if cur == table and assign.match(line):
                hits.append(i)
        if len(hits) != 1:
            return text
        lines[hits[0]] = assign.sub(rf"\g<indent>{new}\g<sp>", lines[hits[0]], count=1)
        return "".join(lines)

    return _apply


def _wrap_bare_string_list(
    key: str, table: str | re.Pattern, *, skip_space: bool = False
) -> Callable[[str], str]:
    """Wrap `key = "val"` into `key = ["val"]` inside [table], preserving the
    quote style and any inline comment. Auto-fix for a single-value list field
    (command="claude", list="dev", files="main.md"). `table` may instead be a
    compiled pattern full-matched against the current `[header]` name (e.g. a
    walker over `[phases.<name>.agent]`) — in that case EVERY matching
    single-line assignment across matching tables is wrapped; the plain-string
    form keeps its exactly-one-hit refusal.

    `skip_space=True` leaves a space-bearing value's line untouched (the walker
    over N sibling tables would otherwise auto-wrap a DIFFERENT phase's unsafe
    `command = "claude -p"` just because some OTHER phase's space-free value
    tripped this migration's detect — silently producing a technically-valid
    but semantically-wrong single-token argv while a sibling manual-only
    Migration also reports it. Used for command/prompt_arg_template, where a
    space is the argv-splitting footgun the manual-only Migration owns; not
    used for list/files, where a space is just an ordinary value character."""
    assign = re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(key)}(?P<sp>[ \t]*=[ \t]*)"
        rf'(?P<q>["\'])(?P<val>.*?)(?P=q)(?P<rest>.*)$'
    )

    def _table_matches(name: str) -> bool:
        if isinstance(table, re.Pattern):
            return bool(table.fullmatch(name))
        return name == table

    def _apply(text: str) -> str:
        lines = text.splitlines(keepends=True)
        cur: str | None = None
        hits: list[int] = []
        for i, line in enumerate(lines):
            h = _TABLE_HEADER.match(line)
            if h:
                cur = h.group("name").strip()
                continue
            if cur is None or not _table_matches(cur):
                continue
            m = assign.match(line)
            if not m or (skip_space and _has_space(m.group("val"))):
                continue
            hits.append(i)
        if not hits:
            return text
        if not isinstance(table, re.Pattern) and len(hits) != 1:
            return text
        for i in hits:
            lines[i] = assign.sub(
                rf"\g<indent>{key}\g<sp>[\g<q>\g<val>\g<q>]\g<rest>", lines[i], count=1
            )
        return "".join(lines)

    return _apply


def _bare_str(parsed: dict, *path: str) -> str | None:
    cur: object = parsed
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur if isinstance(cur, str) else None


def _has_space(s: str | None) -> bool:
    return s is not None and any(c.isspace() for c in s)


def _phase_subtables(parsed: dict) -> list[dict]:
    """The per-phase sub-tables under [phases] (walker for per-phase variants)."""
    phases = parsed.get("phases", {})
    if not isinstance(phases, dict):
        return []
    return [v for v in phases.values() if isinstance(v, dict)]


def _unknown_key_desc(
    table: str,
    allowed: frozenset[str],
    *,
    legacy: frozenset[str] = frozenset(),
    year: str = "0.2.12",
) -> Callable[[dict], str]:
    """Build a `parsed -> str` description for an unknown-key-under-[table]
    Migration: names the exact offending key(s) found in THIS config, and
    degrades to a generic (still correct) instruction when called with no
    config in hand (docgen's static registry render calls every describe with
    `{}`, where there is nothing yet to name). `legacy` excludes key names
    already reported by their own dedicated rename Migration, so a config
    using only a legacy key isn't ALSO reported here."""

    def _describe(parsed: dict) -> str:
        bad = sorted(set(_table(parsed, table)) - allowed - legacy)
        offending = f": {bad}" if bad else ""
        return (
            f"unknown [{table}] key(s) rejected in {year}{offending}; delete them "
            f"(allowed: {sorted(allowed)})"
        )

    return _describe


MIGRATIONS: list[Migration] = [
    Migration(
        detect=lambda p: "rate_limit_action" in _table(p, "runtime"),
        apply=_rename_key("rate_limit_action", "transient_error_action", "runtime"),
        describe="runtime.rate_limit_action → runtime.transient_error_action",
    ),
    Migration(
        detect=lambda p: "orphan_action" in _table(p, "vcs"),
        apply=_rename_key("orphan_action", "dirty_action", "vcs"),
        describe="vcs.orphan_action → vcs.dirty_action",
    ),
    Migration(
        detect=lambda p: "round_timeout_per_phase" in _table(p, "runtime"),
        apply=None,
        describe=(
            "runtime.round_timeout_per_phase (removed 0.1.16) must be moved "
            "manually to [phases.<name>] round_timeout_s"
        ),
    ),
    Migration(
        detect=_has_flat_phase_override,
        apply=None,
        describe=(
            "flat round_timeout_s/disable_pre_round_hooks under [phases.<name>] "
            "should move under a nested [phases.<name>.runtime] sub-table "
            "(the flat form still works as an alias)"
        ),
        advisory=True,  # a valid, permanent alias — never blocks `upgrade`
    ),
    # --- top-level bare-string-list footguns (D1 hard-rejects these; the safe
    # ones auto-fix by wrapping in a single-element list) ---
    Migration(
        detect=lambda p: (
            _bare_str(p, "agent", "command") is not None
            and not _has_space(_bare_str(p, "agent", "command"))
        ),
        apply=_wrap_bare_string_list("command", "agent"),
        describe='agent.command "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: _has_space(_bare_str(p, "agent", "command")),
        apply=None,
        describe=(
            "[agent] command is a quoted string with spaces; rewrite it as an "
            'argv list, e.g. command = ["claude", "-p"] '
            "(auto-split is unsafe — shell quoting rules differ)"
        ),
    ),
    Migration(
        detect=lambda p: (
            _bare_str(p, "agent", "prompt_arg_template") is not None
            and not _has_space(_bare_str(p, "agent", "prompt_arg_template"))
        ),
        apply=_wrap_bare_string_list("prompt_arg_template", "agent"),
        describe='agent.prompt_arg_template "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: _has_space(_bare_str(p, "agent", "prompt_arg_template")),
        apply=None,
        describe=(
            "[agent] prompt_arg_template is a quoted string with spaces; rewrite "
            'it as a list, e.g. prompt_arg_template = ["-p", "{prompt}"]'
        ),
    ),
    Migration(
        detect=lambda p: _bare_str(p, "phases", "list") is not None,
        apply=_wrap_bare_string_list("list", "phases"),
        describe='phases.list "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: _bare_str(p, "prompt", "files") is not None,
        apply=_wrap_bare_string_list("files", "prompt"),
        describe='prompt.files "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: _bare_str(p, "monitor", "auto_stop_on") is not None,
        apply=_wrap_bare_string_list("auto_stop_on", "monitor"),
        describe='monitor.auto_stop_on "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: _bare_str(p, "plugins", "disable") is not None,
        apply=_wrap_bare_string_list("disable", "plugins"),
        describe='plugins.disable "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: _table(p, "agent").get("command") == [],
        apply=None,
        describe=(
            '[agent] command is empty; set a real argv list, e.g. command = ["claude"] '
            "(no auto-fix — a real value is needed)"
        ),
    ),
    Migration(
        detect=lambda p: _table(p, "prompt").get("files") == [],
        apply=None,
        describe=(
            "empty top-level [prompt] files; give it real paths or remove the key "
            "(per-phase [phases.<name>.prompt] files = [] stays valid)"
        ),
    ),
    # --- per-phase variants (walker over [phases.<name>.*] sub-tables) ---
    Migration(
        detect=lambda p: any(
            isinstance(sub.get("agent"), dict) and sub["agent"].get("command") == []
            for sub in _phase_subtables(p)
        ),
        apply=None,
        describe=(
            "a [phases.<name>.agent] command is empty; set a real argv list "
            "(no auto-fix — a real value is needed)"
        ),
    ),
    Migration(
        detect=lambda p: any(
            _bare_str(sub, "agent", "command") is not None
            and not _has_space(_bare_str(sub, "agent", "command"))
            for sub in _phase_subtables(p)
        ),
        apply=_wrap_bare_string_list(
            "command", re.compile(r"phases\.[^.\]]+\.agent"), skip_space=True
        ),
        describe='phases.<name>.agent.command "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: any(
            _has_space(_bare_str(sub, "agent", "command"))
            or _has_space(_bare_str(sub, "agent", "prompt_arg_template"))
            for sub in _phase_subtables(p)
        ),
        apply=None,
        describe=(
            "a [phases.<name>.agent] command/prompt_arg_template is a quoted string "
            "with spaces; rewrite it as an argv list (auto-split is unsafe)"
        ),
    ),
    Migration(
        detect=lambda p: any(
            _bare_str(sub, "agent", "prompt_arg_template") is not None
            and not _has_space(_bare_str(sub, "agent", "prompt_arg_template"))
            for sub in _phase_subtables(p)
        ),
        apply=_wrap_bare_string_list(
            "prompt_arg_template", re.compile(r"phases\.[^.\]]+\.agent"), skip_space=True
        ),
        describe='phases.<name>.agent.prompt_arg_template "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: any(
            _bare_str(sub, "prompt", "files") is not None for sub in _phase_subtables(p)
        ),
        apply=_wrap_bare_string_list("files", re.compile(r"phases\.[^.\]]+\.prompt")),
        describe='phases.<name>.prompt.files "x" → ["x"]',
    ),
    Migration(
        detect=lambda p: any(
            isinstance(sub.get("schedule"), dict)
            and set(sub["schedule"]) - _SCHEDULE_ALLOWED_FIELDS
            for sub in _phase_subtables(p)
        ),
        apply=None,
        describe=(
            f"unknown [phases.<name>.schedule] key(s) rejected in 0.2.12; delete them "
            f"(allowed: {sorted(_SCHEDULE_ALLOWED_FIELDS)})"
        ),
    ),
    # --- top-level unknown-key / threshold footguns (manual-only: naming the
    # exact offending key(s) requires the loader's own ConfigError) ---
    Migration(
        detect=lambda p: bool(set(_table(p, "prompt")) - _PROMPT_ALLOWED_FIELDS),
        apply=None,
        describe=_unknown_key_desc("prompt", _PROMPT_ALLOWED_FIELDS),
    ),
    Migration(
        detect=lambda p: bool(set(_table(p, "schedule")) - _SCHEDULE_ALLOWED_FIELDS),
        apply=None,
        describe=_unknown_key_desc("schedule", _SCHEDULE_ALLOWED_FIELDS),
    ),
    Migration(
        detect=lambda p: (
            (_table(p, "monitor").get("anomaly_repetitive_threshold") or 0) > 0
            and (_table(p, "monitor").get("anomaly_repetitive_window") or 0) > 0
            and _table(p, "monitor")["anomaly_repetitive_threshold"]
            > _table(p, "monitor")["anomaly_repetitive_window"]
        ),
        apply=None,
        describe=(
            "monitor.anomaly_repetitive_threshold > anomaly_repetitive_window: "
            "lower the threshold or raise the window so the detector can fire"
        ),
    ),
    # --- 0.2.13 strictness completion: table-as-scalar, base-table unknown
    # keys, [phases] scalar keys, per-phase prompt unknown keys, argv {prompt}
    # placeholder. Every rejection below has a config.py counterpart; see
    # config.py's docstring cross-references for the exact raise site. ---
    Migration(
        detect=lambda p: bool(_scalar_tables(p)),
        apply=None,
        describe=lambda p: (
            f"[{', '.join(_scalar_tables(p)) or '<table>'}] given as a scalar, not a "
            f"table; give it real [table] content (no auto-fix possible)"
        ),
    ),
    Migration(
        detect=lambda p: bool(set(_table(p, "agent")) - _AGENT_ALLOWED_FIELDS),
        apply=None,
        describe=_unknown_key_desc("agent", _AGENT_ALLOWED_FIELDS, year="0.2.13"),
    ),
    Migration(
        detect=lambda p: bool(
            set(_table(p, "runtime")) - _RUNTIME_ALLOWED_FIELDS - _RUNTIME_LEGACY_FIELDS
        ),
        apply=None,
        describe=_unknown_key_desc(
            "runtime", _RUNTIME_ALLOWED_FIELDS, legacy=_RUNTIME_LEGACY_FIELDS, year="0.2.13"
        ),
    ),
    Migration(
        detect=lambda p: bool(set(_table(p, "vcs")) - _VCS_ALLOWED_FIELDS - _VCS_LEGACY_FIELDS),
        apply=None,
        describe=_unknown_key_desc(
            "vcs", _VCS_ALLOWED_FIELDS, legacy=_VCS_LEGACY_FIELDS, year="0.2.13"
        ),
    ),
    Migration(
        detect=lambda p: bool(set(_table(p, "monitor")) - _MONITOR_ALLOWED_FIELDS),
        apply=None,
        describe=_unknown_key_desc("monitor", _MONITOR_ALLOWED_FIELDS, year="0.2.13"),
    ),
    Migration(
        detect=lambda p: bool(_phases_scalar_keys(p)),
        apply=None,
        describe=lambda p: (
            f"[phases] key(s) {_phases_scalar_keys(p) or ['<name>']} must be phase "
            "sub-tables ([phases.<name>]); only 'list'/'phase_policy' are scalar "
            "[phases] fields"
        ),
    ),
    Migration(
        detect=lambda p: any(
            isinstance(sub.get("prompt"), dict)
            and set(sub["prompt"]) - _PHASE_PROMPT_ALLOWED_FIELDS
            for sub in _phase_subtables(p)
        ),
        apply=None,
        describe=(
            f"unknown [phases.<name>.prompt] key(s) rejected in 0.2.13; delete them "
            f"(allowed: {sorted(_PHASE_PROMPT_ALLOWED_FIELDS)})"
        ),
    ),
    Migration(
        detect=lambda p: _agent_missing_prompt_placeholder(_table(p, "agent")),
        apply=None,
        describe=(
            "[agent] prompt_arg_template has no {prompt} placeholder; the prompt is "
            "never delivered to the agent — add {prompt} to one of the argv tokens"
        ),
    ),
    Migration(
        detect=lambda p: any(_phase_missing_prompt_placeholder(sub) for sub in _phase_subtables(p)),
        apply=None,
        describe=(
            "a [phases.<name>.agent] prompt_arg_template has no {prompt} placeholder; "
            "add one, or set that phase's prompt.files = [] if it truly sends no prompt"
        ),
    ),
]


def _describe(m: Migration, parsed: dict) -> str:
    """Resolve a Migration's report line: a plain string as-is, or a
    `parsed -> str` callable invoked with the actual config (so it can name
    the exact offending key(s) detected in THIS file)."""
    return m.describe(parsed) if callable(m.describe) else m.describe


def run_migrations(text: str, parsed: dict) -> MigrationResult:
    new_text = text
    applied: list[str] = []
    manual: list[str] = []
    advisory: list[str] = []
    for m in MIGRATIONS:
        if not m.detect(parsed):
            continue
        desc = _describe(m, parsed)
        if m.advisory:
            advisory.append(desc)
            continue
        if m.apply is None:
            manual.append(desc)
            continue
        rewritten = m.apply(new_text)
        if rewritten == new_text:
            # Detected but the line-anchored rename matched nothing (e.g. a
            # dotted-key `runtime.rate_limit_action = ...` at top level) —
            # route to manual so we never report "applied" on an unchanged file.
            manual.append(desc)
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
                desc + " (target key already present — remove the deprecated key manually)"
            )
            continue
        new_text = rewritten
        applied.append(desc)
    return MigrationResult(new_text=new_text, applied=applied, manual=manual, advisory=advisory)
