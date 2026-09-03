"""Generic TOML-value validation primitives shared across every table parser.

Nothing here knows which table it's validating (the caller supplies the
``field``/``label`` string for the error message) — that's what keeps this
module reusable across ``parsers.py``'s one-parser-per-table split instead of
each parser re-inventing "reject a bare string where a list is required."
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_runner.config.errors import ConfigError


def _reject_control_chars(value: str, field: str) -> None:
    """Fail closed on a control or other non-printable character (``\\n``,
    ``\\r``, ``\\t``, NUL, ...).

    ``work_dir``/``log_dir``/the config path itself are interpolated verbatim
    into a rendered systemd unit (``service_unit.py``'s ``WorkingDirectory=``
    / ``--config``); an embedded newline injects an arbitrary extra directive
    (e.g. ``User=root``). Checked here at load AND again at render time
    (``service_unit.py`` imports this too) since a ``Config`` can be built
    directly, bypassing this loader entirely.
    """
    if not value.isprintable():
        raise ConfigError(f"{field}: contains a control or non-printable character")


def _require(d: dict, *path: str) -> object:
    cur: object = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            raise ConfigError(f"missing required field: {'.'.join(path)}")
        cur = cur[p]
    return cur


def _require_table(raw: dict, key: str, *, label: str | None = None) -> dict:
    """Return ``raw[key]`` as a dict, defaulting to ``{}`` when absent.

    Raises ``ConfigError`` when ``key`` is present but not a table (the
    table-as-scalar footgun, e.g. ``agent = 1`` instead of ``[agent]``) —
    every downstream parser assumes a dict and would otherwise crash with a
    bare AttributeError/TypeError deep inside its own field lookups.

    ``label`` overrides the table name in the error message (e.g.
    ``"agent.env"`` when ``key`` is just ``"env"`` off a nested dict) so a
    nested-table-as-scalar footgun still names its real dotted path instead
    of the bare leaf key.
    """
    value = raw.get(key, {})
    if not isinstance(value, dict):
        name = label or key
        raise ConfigError(
            f"[{name}] must be a table, got {type(value).__name__} ({value!r}); "
            f"run `agent-runner migrate`"
        )
    return value


def _reject_unknown_fields(d: dict, allowed: frozenset[str], label: str) -> None:
    """Raise ``ConfigError`` if ``d`` has any key outside ``allowed``.

    ``label`` names the TOML table in the message (e.g. ``"runtime"`` or
    ``"phases.dev.prompt"``) without its own brackets -- this wraps it. The
    single source for the "unknown [X] field(s): ...; run `agent-runner
    migrate`" wording shared by every base-table and per-phase-sub-table
    strictness check.
    """
    unknown = set(d) - allowed
    if unknown:
        raise ConfigError(
            f"unknown [{label}] field(s): {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}; run `agent-runner migrate`"
        )


def _expand_path(s: str, project_name: str) -> Path:
    return Path(s.replace("{project}", project_name)).expanduser()


def _resolve_against_work_dir(p: Path | None, work_dir: Path) -> Path | None:
    """Return absolute path: None passes through, abs unchanged, relative joined to work_dir."""
    if p is None:
        return None
    return p if p.is_absolute() else (work_dir / p).resolve()


def _expand_and_resolve(s: str, project_name: str, work_dir: Path) -> Path:
    """Expand ~ and {project} in s, then resolve relative paths against work_dir."""
    return _resolve_against_work_dir(_expand_path(s, project_name), work_dir)  # type: ignore[return-value]


def _require_positive_int(value: Any, *, field: str) -> int:
    """Validate a TOML value is a positive int. Rejects bool (subclass of int
    in Python, would silently coerce e.g. ``true`` → 1) and any non-int."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field}: must be an integer, got {type(value).__name__} ({value!r})")
    if value <= 0:
        raise ConfigError(f"{field}: must be positive, got {value}")
    return value


def _require_bool(value: Any, *, field: str) -> bool:
    """Validate a TOML value is a bool. Distinct from int (in TOML, bool ≠ int)."""
    if not isinstance(value, bool):
        raise ConfigError(f"{field}: must be a bool, got {type(value).__name__} ({value!r})")
    return value


def _require_non_negative_int(value: Any, *, field: str) -> int:
    """Validate a TOML value is a non-negative int (allows 0). Rejects bool
    and any non-int. Sibling of _require_positive_int where 0 has meaning
    (e.g. opt-out / disable)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field}: must be an integer, got {type(value).__name__} ({value!r})")
    if value < 0:
        raise ConfigError(f"{field}: must be >= 0, got {value}")
    return value


def _require_str_list(value: Any, *, field: str) -> list[str]:
    """Validate a TOML value is a list (not a bare string or scalar) and return
    its elements as strings. The bare-string case is the footgun this rejects:
    ``command = "claude"`` would otherwise ``list()``-explode into
    ``['c','l','a','u','d','e']``. Message names ``agent-runner migrate`` so a
    rejected pre-0.2.12 config points straight at the fix."""
    if isinstance(value, str):
        raise ConfigError(
            f"{field}: must be a list, not a bare string {value!r}; run `agent-runner migrate`"
        )
    if not isinstance(value, list):
        raise ConfigError(
            f"{field}: must be a list, got {type(value).__name__}; run `agent-runner migrate`"
        )
    return [str(x) for x in value]


def _require_pct(value: Any, *, field: str) -> float:
    """Validate a TOML value is a percent in [0, 100]. Accepts int and float —
    TOML parses ``90`` as int and the shipped tuning tables recommend bare ints.
    Rejects bool (subclass of int, would silently coerce ``true`` -> 1.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field}: must be a number, got {type(value).__name__} ({value!r})")
    v = float(value)
    # Keep the chained form: `v < 0 or v > 100` looks equivalent but admits nan,
    # which then disables the detector as silently as an out-of-range literal.
    if not 0.0 <= v <= 100.0:
        raise ConfigError(f"{field}: must be between 0 and 100, got {v}")
    return v


def _validate_regex_list(value: Any, *, field: str) -> list[str]:
    """Validate a list of regex pattern strings (each must compile). Returns the
    raw strings unchanged; callers compile when they need ``re.Pattern`` objects."""
    if not isinstance(value, list):
        raise ConfigError(f"{field}: expected a list of regex strings, got {type(value).__name__}")
    out: list[str] = []
    for p in value:
        if not isinstance(p, str):
            raise ConfigError(
                f"{field}: each pattern must be a string, got {type(p).__name__}: {p!r}"
            )
        try:
            re.compile(p)
        except re.error as e:
            raise ConfigError(f"{field}: invalid regex {p!r}: {e}") from e
        out.append(p)
    return out
