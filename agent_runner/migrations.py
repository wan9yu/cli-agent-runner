"""Executable config migrations: a registry of transforms, each detecting a
removed/renamed key in the parsed TOML and either rewriting it (targeted regex on
raw text, so comments/formatting survive -- no TOML writer) or reporting a manual fix."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_runner.config import (
    _AGENT_ALLOWED_FIELDS,
    _CURRENT_SCHEMA_VERSION,
    _MONITOR_ALLOWED_FIELDS,
    _MONITOR_HOST_HEALTH_ALLOWED_FIELDS,
    _PHASE_PROMPT_ALLOWED_FIELDS,
    _PLUGINS_ALLOWED_FIELDS,
    _PROMPT_ALLOWED_FIELDS,
    _RUNTIME_ALLOWED_FIELDS,
    _SCHEDULE_ALLOWED_FIELDS,
    _VCS_ALLOWED_FIELDS,
)

# Legacy keys already handled by their own dedicated (auto-fixable) Migration
# below -- excluded from the generic "unknown key" detectors so a legacy-only
# config isn't double-reported as both a rename AND an unknown key.
_RUNTIME_LEGACY_FIELDS = frozenset({"round_timeout_per_phase", "rate_limit_action"})
_VCS_LEGACY_FIELDS = frozenset({"orphan_action"})
# 0.3.9: these each get their own dedicated, specifically-worded Migration
# below -- excluded here so a config using one isn't ALSO reported generically.
_PLUGINS_LEGACY_FIELDS = frozenset({"sandbox", "pin", "spawn_override_allow"})

# [plugins] disable used to key on a hook's OWN .name (e.g. the PostRoundHook
# class's `name` attribute); the PluginManifest ABI disables by the owning
# plugin's manifest name instead. Auto-renaming would mean guessing operator
# intent from an arbitrary list-value string, so this is MANUAL-only (below).
_OLD_PLUGIN_DISABLE_NAMES = {
    # Terminal names are the CURRENT plugin (manifest) names -- claude's is
    # "claude" as of the 0.3.9 rename (was "claude_rate_limit"), so the ancient
    # hook-level name maps straight to the final name, not the intermediate one.
    "claude_error_detector": "claude",
    "gemini_error_detector": "gemini",
    "codewhale_error_detector": "codewhale",
    "kimi_error_detector": "kimi",
    "pi_error_detector": "pi",
}

# 0.3.9: the claude builtin plugin's manifest+entry-point name went
# claude_rate_limit -> claude, so a config disabling the old name in
# [plugins] disable would otherwise become silently ineffective.
_RENAMED_PLUGIN_DISABLE_NAMES = {"claude_rate_limit": "claude"}


def _old_plugin_disable_names(p: dict) -> dict[str, str]:
    """Old->new name mapping for any 0.2.x hook-level name found in
    ``[plugins] disable``; empty when none are present."""
    disable = _table(p, "plugins").get("disable")
    listed = disable if isinstance(disable, list) else []
    return {k: v for k, v in _OLD_PLUGIN_DISABLE_NAMES.items() if k in listed}


def _renamed_plugin_disable_names(p: dict) -> dict[str, str]:
    """Old->new mapping for any 0.3.x-renamed plugin name found in
    ``[plugins] disable`` (currently only claude_rate_limit -> claude); empty
    when none are present."""
    disable = _table(p, "plugins").get("disable")
    listed = disable if isinstance(disable, list) else []
    return {k: v for k, v in _RENAMED_PLUGIN_DISABLE_NAMES.items() if k in listed}


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
    `[agent]`) — the table-as-scalar footgun hard-rejects at load. No
    auto-fix is possible; real table content is needed."""
    return [t for t in _TOP_LEVEL_TABLES if t in p and not isinstance(p[t], dict)]


# Flat override fields that also live under a nested [phases.<name>.runtime]
# sub-table. The flat form is a permanent alias, so this is guidance only.
_PHASE_RUNTIME_FLAT_FIELDS = ("round_budget_s",)


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


def _rename_key(old: str, new: str, table: str | re.Pattern) -> Callable[[str], str]:
    """Rename `old = ...` to `new = ...` inside [table]. `table` may be a
    compiled pattern fullmatched against the current `[header]` name (e.g. a
    per-phase walker) — every matching table's hit is rewritten. A literal
    str `table` keeps the original exactly-one-hit-or-refuse discipline."""
    assign = re.compile(rf"^(?P<indent>[ \t]*){re.escape(old)}(?P<sp>[ \t]*=)")

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
            if cur is not None and _table_matches(cur) and assign.match(line):
                hits.append(i)
        if not hits:
            return text
        if not isinstance(table, re.Pattern) and len(hits) != 1:
            return text
        for i in hits:
            lines[i] = assign.sub(rf"\g<indent>{new}\g<sp>", lines[i], count=1)
        return "".join(lines)

    return _apply


def _drop_key(key: str, table: str | re.Pattern) -> Callable[[str], str]:
    """Delete `key = ...` (the whole line) from [table] -- for a key removed
    with no replacement (contrast `_rename_key`, which keeps the value under
    a new name). Same table-matching + single-hit-or-refuse discipline as
    `_rename_key`: a Pattern `table` drops every matching hit, a literal str
    `table` refuses (routing the caller to manual) unless there's one hit."""
    assign = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=")

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
            if cur is not None and _table_matches(cur) and assign.match(line):
                hits.append(i)
        if not hits:
            return text
        if not isinstance(table, re.Pattern) and len(hits) != 1:
            return text
        for i in sorted(hits, reverse=True):
            del lines[i]
        return "".join(lines)

    return _apply


def _relocate_key(old: str, new: str, from_table: str, to_table: str) -> Callable[[str], str]:
    """Move `old = value` out of [from_table] into [to_table] as `new = value`,
    creating [to_table] (appended at EOF) if absent. Refuses (returns text
    unchanged, routing to manual) unless the source line is a single
    unambiguous hit in [from_table] — same refusal discipline as _rename_key."""
    assign = re.compile(rf"^(?P<indent>[ \t]*){re.escape(old)}(?P<sp>[ \t]*=[ \t]*)(?P<rest>.*)$")

    def _apply(text: str) -> str:
        lines = text.splitlines(keepends=True)
        cur: str | None = None
        hit: int | None = None
        for i, line in enumerate(lines):
            h = _TABLE_HEADER.match(line)
            if h:
                cur = h.group("name").strip()
                continue
            if cur == from_table and assign.match(line):
                if hit is not None:
                    return text
                hit = i
        if hit is None:
            return text
        m = assign.match(lines[hit])
        trailing_nl = "\n" if lines[hit].endswith("\n") else ""
        new_line = f"{new}{m.group('sp')}{m.group('rest').rstrip(chr(10))}{trailing_nl}"
        del lines[hit]
        dest_idx: int | None = None
        cur = None
        for i, line in enumerate(lines):
            h = _TABLE_HEADER.match(line)
            if h:
                if dest_idx is not None and cur == to_table:
                    break
                cur = h.group("name").strip()
                if cur == to_table:
                    dest_idx = i
                continue
            if cur == to_table:
                dest_idx = i
        if dest_idx is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"[{to_table}]\n")
            lines.append(new_line)
        else:
            lines.insert(dest_idx + 1, new_line)
        return "".join(lines)

    return _apply


def _wrap_bare_string_list(
    key: str, table: str | re.Pattern, *, skip_space: bool = False
) -> Callable[[str], str]:
    """Wrap `key = "val"` into `key = ["val"]` inside [table], preserving quote
    style and any inline comment -- the auto-fix for a single-value list field
    (command="claude", list="dev"). `table` may instead be a compiled pattern
    full-matched against the current `[header]` (e.g. a walker over
    `[phases.<name>.agent]`), wrapping EVERY matching hit; a plain-string
    `table` keeps the exactly-one-hit refusal.

    `skip_space=True` leaves a space-bearing value untouched so a walker over
    N sibling tables doesn't auto-wrap a DIFFERENT phase's unsafe
    `command = "claude -p"` into a wrong single-token argv just because some
    OTHER phase's space-free value tripped detect. Used for
    command/prompt_arg_template (a space there is the argv-splitting footgun
    a sibling manual-only Migration owns); not list/files, where a space is
    just an ordinary value character."""
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
    config in hand (docgen's static registry render calls every describe
    with `{}`). `legacy` excludes key names already reported by their own
    dedicated rename Migration, so a legacy-only config isn't ALSO reported."""

    def _describe(parsed: dict) -> str:
        bad = sorted(set(_table(parsed, table)) - allowed - legacy)
        offending = f": {bad}" if bad else ""
        return (
            f"unknown [{table}] key(s) rejected in {year}{offending}; delete them "
            f"(allowed: {sorted(allowed)})"
        )

    return _describe


def _unknown_key_desc_nested(
    label: str,
    path: tuple[str, ...],
    allowed: frozenset[str],
    *,
    year: str = "0.2.12",
) -> Callable[[dict], str]:
    """Nested-table variant of ``_unknown_key_desc``: ``_table`` only reads a
    TOP-LEVEL key, so this walks ``path`` (e.g. ``("monitor", "host_health")``)
    through nested ``_table`` calls before diffing against ``allowed``, with
    ``label`` as the dotted-bracket name in the message. MANUAL by design like
    every sibling: auto-deleting an unknown threshold key would silently
    discard the operator's intended value — the harm this rejection prevents."""

    def _describe(parsed: dict) -> str:
        cur = parsed
        for seg in path:
            cur = _table(cur, seg)
        bad = sorted(set(cur) - allowed)
        offending = f": {bad}" if bad else ""
        return (
            f"unknown [{label}] key(s) rejected in {year}{offending}; delete them "
            f"(allowed: {sorted(allowed)})"
        )

    return _describe


# 0.3.0: [monitor.host_health] regroup — (old flat key, new key, destination
# sub-table) for each of the 9 keys relocated into disk/memory/pressure.
_HOST_HEALTH_RELOCATIONS = (
    ("mem_avail_min_mb", "avail_min_mb", "memory"),
    ("disk_warning_pct", "warning_pct", "disk"),
    ("disk_critical_pct", "critical_pct", "disk"),
    ("swap_sout_noise_floor_mb", "swap_out_noise_floor_mb", "memory"),
    ("mem_free_low_mb", "free_low_mb", "memory"),
    ("psi_full_avg10_critical", "full_avg10_critical", "pressure"),
    ("psi_some_avg10_warning", "some_avg10_warning", "pressure"),
    ("mem_critical_consecutive_samples", "critical_consecutive_samples", "pressure"),
    ("in_round_mem_terminate", "in_round_terminate", "pressure"),
)


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
            "manually to [phases.<name>] round_budget_s"
        ),
    ),
    # --- 0.3.0: [runtime]/[phases.<name>] round_timeout_s → round_budget_s ---
    Migration(
        detect=lambda p: "round_timeout_s" in _table(p, "runtime"),
        apply=_rename_key("round_timeout_s", "round_budget_s", "runtime"),
        describe="runtime.round_timeout_s → runtime.round_budget_s",
    ),
    Migration(
        detect=lambda p: any("round_timeout_s" in sub for sub in _phase_subtables(p)),
        apply=_rename_key("round_timeout_s", "round_budget_s", re.compile(r"phases\.[^.\]]+")),
        describe="phases.<name>.round_timeout_s → phases.<name>.round_budget_s",
    ),
    Migration(
        detect=lambda p: any(
            isinstance(sub.get("runtime"), dict) and "round_timeout_s" in sub["runtime"]
            for sub in _phase_subtables(p)
        ),
        apply=_rename_key(
            "round_timeout_s", "round_budget_s", re.compile(r"phases\.[^.\]]+\.runtime")
        ),
        describe=("phases.<name>.runtime.round_timeout_s → phases.<name>.runtime.round_budget_s"),
    ),
    # --- 0.3.9: runtime.disable_pre_round_hooks dropped (its only consumer,
    # the PreRoundHook plugin seam, was removed — the flag had become a no-op
    # config knob). Auto-dropped in all three shapes it could appear in. ---
    Migration(
        detect=lambda p: "disable_pre_round_hooks" in _table(p, "runtime"),
        apply=_drop_key("disable_pre_round_hooks", "runtime"),
        describe="runtime.disable_pre_round_hooks (removed 0.3.9 — PreRoundHook seam dropped)",
    ),
    Migration(
        detect=lambda p: any("disable_pre_round_hooks" in sub for sub in _phase_subtables(p)),
        apply=_drop_key("disable_pre_round_hooks", re.compile(r"phases\.[^.\]]+")),
        describe=(
            "phases.<name>.disable_pre_round_hooks (removed 0.3.9 — PreRoundHook seam dropped)"
        ),
    ),
    Migration(
        detect=lambda p: any(
            isinstance(sub.get("runtime"), dict) and "disable_pre_round_hooks" in sub["runtime"]
            for sub in _phase_subtables(p)
        ),
        apply=_drop_key("disable_pre_round_hooks", re.compile(r"phases\.[^.\]]+\.runtime")),
        describe=(
            "phases.<name>.runtime.disable_pre_round_hooks "
            "(removed 0.3.9 — PreRoundHook seam dropped)"
        ),
    ),
    Migration(
        detect=_has_flat_phase_override,
        apply=None,
        describe=(
            "flat round_budget_s under [phases.<name>] should move under a "
            "nested [phases.<name>.runtime] sub-table (the flat form still "
            "works as an alias)"
        ),
        advisory=True,  # a valid, permanent alias — never blocks `upgrade`
    ),
    # --- 0.3.9: the third-party sandbox trampoline + spawn seam is removed (0
    # third-party plugins ever existed) — MANUAL-only: `pin`/`spawn_override_allow`
    # can be multi-line TOML values a single-line auto-drop could corrupt. ---
    Migration(
        detect=lambda p: "sandbox" in _table(p, "plugins"),
        apply=None,
        describe=(
            "plugins.sandbox (removed 0.3.9 — the third-party sandbox trampoline "
            "was dropped); delete the key"
        ),
    ),
    Migration(
        detect=lambda p: "spawn_override_allow" in _table(p, "plugins"),
        apply=None,
        describe=(
            "plugins.spawn_override_allow (removed 0.3.9 — the SpawnHook seam "
            "was dropped); delete the key"
        ),
    ),
    Migration(
        detect=lambda p: "pin" in _table(p, "plugins"),
        apply=None,
        describe=(
            "[plugins.pin] (removed 0.3.9 — the third-party checksum-pin gate "
            "was dropped); delete the table"
        ),
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
        detect=lambda p: bool(_old_plugin_disable_names(p)),
        apply=None,
        describe=lambda p: (
            "[plugins] disable names a 0.2.x hook-level name; 0.3.0's PluginManifest "
            "ABI disables by plugin name instead — rewrite manually: "
            # No config in hand (docgen renders against {}) degrades to the
            # full rename table, same fallback shape as _scalar_tables(p) or
            # '<table>' above -- never a dangling "{}".
            f"{_old_plugin_disable_names(p) or _OLD_PLUGIN_DISABLE_NAMES}"
        ),
    ),
    # 0.3.9 claude plugin rename. MANUAL-only, matching the sibling above: a
    # quoted element inside a list VALUE isn't something the key-anchored
    # rewrite helpers touch. Reported by `migrate` and blocks `upgrade`, so
    # `disable = ["claude_rate_limit"]` is never left silently ineffective.
    Migration(
        detect=lambda p: bool(_renamed_plugin_disable_names(p)),
        apply=None,
        describe=lambda p: (
            "[plugins] disable names a plugin renamed in 0.3.9; rewrite manually: "
            f"{_renamed_plugin_disable_names(p) or _RENAMED_PLUGIN_DISABLE_NAMES}"
        ),
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
    # --- Strictness completion: table-as-scalar, base-table unknown keys,
    # [phases] scalar keys, per-phase prompt unknown keys, argv {prompt}
    # placeholder -- each rejection below has a config.py raise-site counterpart. ---
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
    # 0.3.9: [plugins]'s `.raw` catch-all is gone -- any key it used to
    # silently absorb is now rejected outright, like every sibling table.
    Migration(
        detect=lambda p: bool(
            set(_table(p, "plugins")) - _PLUGINS_ALLOWED_FIELDS - _PLUGINS_LEGACY_FIELDS
        ),
        apply=None,
        describe=_unknown_key_desc(
            "plugins", _PLUGINS_ALLOWED_FIELDS, legacy=_PLUGINS_LEGACY_FIELDS, year="0.3.9"
        ),
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
    # --- 0.3.0: [monitor.host_health] regroup — 9 flat keys relocate into
    # disk/memory/pressure sub-tables. Each targets a different old key, so
    # they are order-insensitive and converge in a single run_migrations pass
    # (see _relocate_key's docstring). ---
    *[
        Migration(
            detect=lambda p, old=old: old in _table(_table(p, "monitor"), "host_health"),
            apply=_relocate_key(old, new, "monitor.host_health", f"monitor.host_health.{sub}"),
            describe=f"monitor.host_health.{old} → monitor.host_health.{sub}.{new}",
        )
        for old, new, sub in _HOST_HEALTH_RELOCATIONS
    ],
    # --- Strictness completion: [monitor.host_health] unknown keys (not one
    # of the three sub-tables or the 9 relocated names). MANUAL like every
    # sibling above -- auto-deleting a threshold key would discard operator intent. ---
    Migration(
        detect=lambda p: bool(
            set(_table(_table(p, "monitor"), "host_health")) - _MONITOR_HOST_HEALTH_ALLOWED_FIELDS
        ),
        apply=None,
        describe=_unknown_key_desc_nested(
            "monitor.host_health",
            ("monitor", "host_health"),
            _MONITOR_HOST_HEALTH_ALLOWED_FIELDS,
            year="0.3.0",
        ),
    ),
]


def _describe(m: Migration, parsed: dict) -> str:
    """Resolve a Migration's report line: a plain string as-is, or a
    `parsed -> str` callable invoked with the actual config (so it can name
    the exact offending key(s) detected in THIS file)."""
    return m.describe(parsed) if callable(m.describe) else m.describe


def _run_migrations_pass(text: str, parsed: dict) -> tuple[str, list[str], list[str], list[str]]:
    """One detect+rewrite pass over ``MIGRATIONS`` against a fixed ``parsed``.
    Returns ``(new_text, applied, manual, advisory)``."""
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
    return new_text, applied, manual, advisory


# A rewrite can expose a footgun that only a re-parse reveals, so migration runs
# to a fixpoint (see run_migrations). Generous bound: each pass must apply at
# least one NEW rewrite to continue, and the footgun count is small, so only a
# pathological apply/detect cycle could exhaust this.
_MAX_MIGRATION_PASSES = 12


def run_migrations(text: str, parsed: dict) -> MigrationResult:
    """Apply migrations to a FIXPOINT. A single pass can rewrite one footgun
    into a shape that only THEN trips a second detector — e.g. a bare-string
    ``prompt_arg_template = "-p"`` is wrapped to ``["-p"]``, which the re-parse
    reveals has no ``{prompt}`` placeholder; a one-shot pass would exit clean
    on a config that still won't ``load_config``. So re-parse and re-run
    detectors until a pass applies no further rewrite; only that terminal
    pass's ``manual``/``advisory`` reports are returned (an intermediate pass
    may flag a blocker a later rewrite resolves). Bounded so an apply/detect
    cycle can't loop forever."""
    new_text = text
    cur = parsed
    applied: list[str] = []
    for _ in range(_MAX_MIGRATION_PASSES):
        rewritten, pass_applied, manual, advisory = _run_migrations_pass(new_text, cur)
        applied.extend(pass_applied)
        if rewritten == new_text:
            stamped, version_applied = _stamp_schema_version(new_text, cur)
            if version_applied:
                applied.append(version_applied)
            return MigrationResult(
                new_text=stamped, applied=applied, manual=manual, advisory=advisory
            )
        new_text = rewritten
        cur = tomllib.loads(rewritten)  # each rewrite was TOML-validated in the pass
    raise RuntimeError(
        f"config migration did not converge after {_MAX_MIGRATION_PASSES} passes — "
        "two migrations appear to undo each other; please file a bug"
    )


_SCHEMA_VERSION_ASSIGN = re.compile(r"^(?P<indent>[ \t]*)schema_version[ \t]*=.*")


def _stamp_schema_version(text: str, parsed: dict) -> tuple[str, str | None]:
    """Stamp ``schema_version = <_CURRENT_SCHEMA_VERSION>``. A no-op (returns
    text unchanged, None) only when a top-level INTEGER ``schema_version`` is
    already current (``type(v) is int`` -- a bool is never "current" even
    though ``True == 1``) or already NEWER (never downgrade a config stamped
    by a future agent-runner). Any other pre-existing top-level
    ``schema_version`` line is REPLACED in place, mirroring ``_rename_key``'s
    discipline -- never blindly prepended, or a non-canonical existing line
    would leave a duplicate key behind (invalid TOML). Only truly absent
    does this prepend a fresh line."""
    version = parsed.get("schema_version")
    if type(version) is int and version >= _CURRENT_SCHEMA_VERSION:
        return text, None
    stamp = f"schema_version = {_CURRENT_SCHEMA_VERSION}"
    lines = text.splitlines(keepends=True)
    hit: int | None = None
    for i, line in enumerate(lines):
        if _TABLE_HEADER.match(line):
            # A top-level key can only precede every table header (TOML
            # syntax) -- once we see one, no top-level schema_version follows.
            break
        if _SCHEMA_VERSION_ASSIGN.match(line):
            hit = i
            break
    if hit is None:
        new_text = f"{stamp}\n{text}"
    else:
        lines[hit] = _SCHEMA_VERSION_ASSIGN.sub(rf"\g<indent>{stamp}", lines[hit], count=1)
        new_text = "".join(lines)
    return new_text, f"stamped schema_version = {_CURRENT_SCHEMA_VERSION}"
