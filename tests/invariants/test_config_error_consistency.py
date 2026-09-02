"""Invariant: every user-facing config error is a ConfigError.

ConfigError's own docstring promises it for "a removed or invalid field", and
docs/configuration.md (§ ConfigError promise and § agent.prompt_delivery) and
CHANGELOG.md all tell operators to expect it.

Two complementary techniques:

- An AST scan pins that config.py never raises a bare ``ValueError`` at a
  literal ``raise`` site — the coarse, structural guard.
- A parametrized BEHAVIORAL suite feeds each footgun shape through
  ``load_config`` and asserts ``ConfigError``. The AST scan is blind to the
  **table-as-scalar** class (``agent = 1`` instead of ``[agent]``): the raise
  happens inside a shared helper (``_require_table``), not as a literal
  ``raise ConfigError(...)`` at every call site the walk would need to see.
  Only running the loader proves the behavior.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._test_helpers import write_min_config

REPO = Path(__file__).resolve().parents[2]
CONFIG_PY = REPO / "agent_runner/config.py"

# Non-config failures that legitimately use a different class.
_ALLOWED_OTHER = {"FileNotFoundError"}

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


def _raised_class_names() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(CONFIG_PY.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                out.append((node.lineno, exc.func.id))
            elif isinstance(exc, ast.Name):
                out.append((node.lineno, exc.id))
    return out


def test_given_config_module_when_scanned_then_user_facing_raises_are_config_error() -> None:
    """No bare ValueError in config.py — every invalid-field path is a ConfigError."""
    raised = _raised_class_names()
    assert raised, "no `raise` sites found in config.py — AST scan broke"  # vacuity-guard
    offenders = [
        f"config.py:{lineno}: raise {name}"
        for lineno, name in sorted(raised)
        if name not in _ALLOWED_OTHER and name != "ConfigError"
    ]
    assert not offenders, (
        "config.py raises a non-ConfigError for a config problem — "
        "ConfigError's docstring and docs/configuration.md:181 promise "
        "ConfigError:\n" + "\n".join(offenders)
    )


def test_given_config_error_when_inspected_then_subclasses_value_error() -> None:
    """The promotion is a widening only because of this relationship. Pin it:
    if ConfigError ever stops subclassing ValueError, every existing
    `pytest.raises(ValueError)` caller silently breaks."""
    from agent_runner.config import ConfigError

    assert issubclass(ConfigError, ValueError)


def test_given_stdin_delivery_with_prompt_token_when_loaded_then_config_error(
    tmp_path: Path,
) -> None:
    """docs/configuration.md § agent.prompt_delivery pins ConfigError at startup here."""
    from agent_runner.config import ConfigError, load_config

    cfg_path = write_min_config(
        tmp_path,
        agent_extra='prompt_delivery = "stdin"\nprompt_arg_template = ["-p", "{prompt}"]\n',
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


# --- table-as-scalar: parametrized behavioral (the AST scan above can't see
# this footgun class — see module docstring) ---


def _write_config_with_scalar_table(tmp_path: Path, table: str) -> Path:
    """A config where `table` is given as a scalar (e.g. ``agent = 1``)
    instead of a ``[table]`` header. The other mandatory tables ([agent],
    [runtime], [prompt]) stay valid so only the table-under-test's shape is
    exercised."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")

    mandatory = {
        "agent": '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n',
        "runtime": f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{log_dir}"\n',
        "prompt": f'[prompt]\nfile = "{prompt_file}"\n',
    }
    # The scalar assignment must come FIRST — TOML has no way to "return to
    # root" after a `[header]` line; any bare `key = value` following one
    # belongs to THAT header's table, not the root table.
    lines = [f"{table} = 1\n"]
    lines += [block for name, block in mandatory.items() if name != table]
    toml = tmp_path / "agent-runner.toml"
    toml.write_text("".join(lines))
    return toml


@pytest.mark.parametrize("table", _TOP_LEVEL_TABLES)
def test_given_top_level_table_as_scalar_when_loaded_then_config_error(
    tmp_path: Path, table: str
) -> None:
    from agent_runner.config import ConfigError, load_config

    cfg_path = _write_config_with_scalar_table(tmp_path, table)
    with pytest.raises(ConfigError):
        load_config(cfg_path)
